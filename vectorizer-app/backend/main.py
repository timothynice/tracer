from fastapi import FastAPI, UploadFile, File, HTTPException, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp
from PIL import Image
import io
import base64
import subprocess
import os
import tempfile
import uuid
import vtracer
import re

app = FastAPI(title="Image Vectorizer API")

# Configure CORS from environment
allowed_origins_env = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,https://tracer-frontend-z5u3.onrender.com")
allowed_origins = [o.strip() for o in allowed_origins_env.split(",") if o.strip()]

# Custom middleware to ensure CORS headers on ALL responses (even errors)
class CORSEnforcerMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        try:
            response = await call_next(request)
        except Exception as e:
            # Catch any exception and create a response with CORS headers
            origin = request.headers.get("origin")
            cors_origin = origin if origin in allowed_origins else (allowed_origins[0] if allowed_origins else "*")
            response = JSONResponse(
                status_code=500,
                content={"detail": "Internal server error"},
                headers={
                    "Access-Control-Allow-Origin": cors_origin,
                    "Access-Control-Allow-Credentials": "true",
                }
            )
        
        # Ensure CORS headers are on the response
        origin = request.headers.get("origin")
        cors_origin = origin if origin in allowed_origins else (allowed_origins[0] if allowed_origins else "*")
        
        # Add CORS headers if not present
        if "Access-Control-Allow-Origin" not in response.headers:
            response.headers["Access-Control-Allow-Origin"] = cors_origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
        
        return response

# Add CORS enforcer middleware FIRST (runs last in reverse order)
app.add_middleware(CORSEnforcerMiddleware)

# Standard CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Exception handlers to ensure CORS headers on all error responses
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Ensure HTTPException responses include CORS headers"""
    origin = request.headers.get("origin")
    cors_origin = origin if origin in allowed_origins else (allowed_origins[0] if allowed_origins else "*")
    
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers={
            "Access-Control-Allow-Origin": cors_origin,
            "Access-Control-Allow-Credentials": "true",
        }
    )

@app.exception_handler(StarletteHTTPException)
async def starlette_exception_handler(request: Request, exc: StarletteHTTPException):
    """Ensure StarletteHTTPException responses include CORS headers"""
    origin = request.headers.get("origin")
    cors_origin = origin if origin in allowed_origins else (allowed_origins[0] if allowed_origins else "*")
    
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers={
            "Access-Control-Allow-Origin": cors_origin,
            "Access-Control-Allow-Credentials": "true",
        }
    )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """Catch-all exception handler with CORS headers"""
    import traceback
    traceback.print_exc()
    
    origin = request.headers.get("origin")
    cors_origin = origin if origin in allowed_origins else (allowed_origins[0] if allowed_origins else "*")
    
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
        headers={
            "Access-Control-Allow-Origin": cors_origin,
            "Access-Control-Allow-Credentials": "true",
        }
    )

class ParameterValidationError(ValueError):
    """Custom exception for parameter validation errors"""
    pass

def validate_potrace_params(params):
    """Validate Potrace parameters with proper bounds checking"""
    # Convert string numbers to actual numbers for HTML range/select inputs
    if 'turdsize' in params and isinstance(params['turdsize'], str):
        try:
            params['turdsize'] = int(params['turdsize']) if '.' not in params['turdsize'] else float(params['turdsize'])
        except (ValueError, TypeError):
            raise ParameterValidationError("turdsize must be a valid number")

    if 'alphamax' in params and isinstance(params['alphamax'], str):
        try:
            params['alphamax'] = float(params['alphamax'])
        except (ValueError, TypeError):
            raise ParameterValidationError("alphamax must be a valid number")

    if 'turdsize' in params:
        if not isinstance(params['turdsize'], (int, float)) or params['turdsize'] < 0 or params['turdsize'] > 100:
            raise ParameterValidationError("turdsize must be a number between 0 and 100")

    if 'alphamax' in params:
        if not isinstance(params['alphamax'], (int, float)) or params['alphamax'] < 0 or params['alphamax'] > 2.0:
            raise ParameterValidationError("alphamax must be a number between 0.0 and 2.0")

    if 'turnpolicy' in params:
        valid_policies = ['black', 'white', 'left', 'right', 'minority', 'majority', 'random']
        if params['turnpolicy'] not in valid_policies:
            raise ParameterValidationError(f"turnpolicy must be one of: {', '.join(valid_policies)}")

    if 'invert' in params:
        if not isinstance(params['invert'], bool):
            raise ParameterValidationError("invert must be a boolean")

    if 'opticurve' in params:
        if not isinstance(params['opticurve'], bool):
            raise ParameterValidationError("opticurve must be a boolean")

def validate_vtracer_params(params):
    """Validate VTracer parameters with proper bounds checking"""
    validations = {
        'colormode': {'type': str, 'values': ['color', 'binary']},
        'color_precision': {'type': (int, float), 'range': (1, 8), 'convert': True},
        'filter_speckle': {'type': (int, float), 'range': (1, 100), 'convert': True},
        'corner_threshold': {'type': (int, float), 'range': (0, 180), 'convert': True},
        'length_threshold': {'type': (int, float), 'range': (0.0, 50.0), 'convert': True},
        'max_iterations': {'type': (int, float), 'range': (1, 100), 'convert': True},
        'splice_threshold': {'type': (int, float), 'range': (0, 180), 'convert': True},
        'path_precision': {'type': (int, float), 'range': (1, 10), 'convert': True}
    }

    for param, validation in validations.items():
        if param in params:
            value = params[param]

            # Convert string numbers to actual numbers for HTML range inputs
            if validation.get('convert') and isinstance(value, str):
                try:
                    # Try int first, then float
                    if '.' in value:
                        value = float(value)
                    else:
                        value = int(value)
                    # Update the params dict with converted value
                    params[param] = value
                except (ValueError, TypeError):
                    raise ParameterValidationError(f"{param} must be a valid number")

            # Type checking
            if 'type' in validation:
                if isinstance(validation['type'], tuple):
                    if not isinstance(value, validation['type']):
                        raise ParameterValidationError(f"{param} must be a {' or '.join([t.__name__ for t in validation['type']])}")
                else:
                    if not isinstance(value, validation['type']):
                        raise ParameterValidationError(f"{param} must be a {validation['type'].__name__}")

            # Value checking
            if 'values' in validation:
                if value not in validation['values']:
                    raise ParameterValidationError(f"{param} must be one of: {', '.join(validation['values'])}")

            # Range checking
            if 'range' in validation:
                min_val, max_val = validation['range']
                if not (min_val <= value <= max_val):
                    raise ParameterValidationError(f"{param} must be between {min_val} and {max_val}")

class VectorizerService:
    def __init__(self):
        pass

    def normalize_svg_dimensions(self, svg_content: str, original_width: int, original_height: int) -> str:
        """Normalize SVG dimensions to consistent scaling with viewBox only (no explicit width/height)"""
        try:
            # Remove explicit width and height attributes to allow CSS scaling to work properly
            svg_content = re.sub(r'\s*width="[^"]*"', '', svg_content)
            svg_content = re.sub(r'\s*height="[^"]*"', '', svg_content)

            # Ensure viewBox is present and matches original dimensions
            viewbox_pattern = r'viewBox="[^"]*"'
            new_viewbox = f'viewBox="0 0 {original_width} {original_height}"'

            if 'viewBox=' in svg_content:
                svg_content = re.sub(viewbox_pattern, new_viewbox, svg_content)
            else:
                # Add viewBox after the opening svg tag
                svg_content = re.sub(r'(<svg[^>]*)', f'\\1 {new_viewbox}', svg_content)

            return svg_content

        except Exception as e:
            print(f"Warning: SVG normalization failed: {e}")
            return svg_content

    async def potrace_vectorize(self, image_bytes: bytes, invert=False, turdsize=2, turnpolicy='minority', alphamax=1.0, opticurve=True) -> str:
        """Vectorize using Potrace (traditional method) with enhanced options"""
        try:
            # Create temporary files
            with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as temp_input:
                temp_input.write(image_bytes)
                temp_input_path = temp_input.name

            temp_svg_path = temp_input_path.replace('.png', '.svg')
            temp_bmp_path = temp_input_path.replace('.png', '.bmp')

            # Convert to BMP and optionally invert
            img = Image.open(temp_input_path).convert('L')  # Convert to grayscale
            original_width, original_height = img.size

            if invert:
                # Invert the image (white becomes black, black becomes white)
                img = Image.eval(img, lambda x: 255 - x)

            img.save(temp_bmp_path)

            # Build potrace command with parameters
            cmd = ['potrace', '-s', '--svg', '--output', temp_svg_path]

            # Add turdsize (filter small speckles)
            cmd.extend(['--turdsize', str(turdsize)])

            # Add turn policy
            cmd.extend(['--turnpolicy', turnpolicy])

            # Add corner threshold
            cmd.extend(['--alphamax', str(alphamax)])

            # Add curve optimization
            if not opticurve:
                cmd.append('--longcurve')  # Turn off curve optimization


            cmd.append(temp_bmp_path)

            

            # Run potrace
            result = subprocess.run(cmd, capture_output=True, text=True)

            if result.returncode != 0:
                raise Exception(f"Potrace failed: {result.stderr}")

            # Read SVG content
            with open(temp_svg_path, 'r') as f:
                svg_content = f.read()

            # Normalize SVG dimensions for consistent scaling
            svg_content = self.normalize_svg_dimensions(svg_content, original_width, original_height)

            # Cleanup
            for path in [temp_input_path, temp_svg_path, temp_bmp_path]:
                if os.path.exists(path):
                    os.unlink(path)

            return svg_content

        except Exception as e:
            # Raise the actual error instead of silently falling back
            raise Exception(f"Potrace processing failed: {str(e)}")

    async def vtracer_vectorize(self, image_bytes: bytes, colormode='color', color_precision=6, filter_speckle=4, corner_threshold=60, length_threshold=4.0, max_iterations=10, splice_threshold=45, path_precision=3) -> str:
        """Vectorize using VTracer (advanced color-preserving method)"""
        temp_input_path = None
        temp_svg_path = None
        try:
            # Use standard Python file writing (more compatible with Rust FFI)
            # Write to /tmp explicitly to ensure visibility
            temp_dir = '/tmp'
            if not os.path.exists(temp_dir):
                temp_dir = tempfile.gettempdir()
            
            # Always convert to PNG for VTracer compatibility
            # VTracer's Rust library has issues with some JPEG files, so we
            # standardize on PNG format regardless of input format
            unique_id = str(uuid.uuid4())
            temp_input_path = os.path.join(temp_dir, f'vtracer_input_{unique_id}.png')
            temp_svg_path = os.path.join(temp_dir, f'vtracer_output_{unique_id}.svg')

            # Convert to PNG using PIL to ensure VTracer compatibility
            try:
                img = Image.open(io.BytesIO(image_bytes))
                img = img.convert('RGB')  # Ensure RGB mode for consistent PNG output
                img.save(temp_input_path, 'PNG', optimize=True)
                img.close()
            except Exception as e:
                raise Exception(f"Failed to convert image to PNG format: {str(e)}")

            # Ensure file is written and accessible
            
            # Set explicit permissions - ensure file is readable by all (Rust might run as different user)
            os.chmod(temp_input_path, 0o644)  # rw-r--r--
            
            # Convert to absolute paths using realpath to resolve any symlinks
            temp_input_path = os.path.realpath(os.path.abspath(temp_input_path))
            # Build svg path from base name regardless of input extension
            base_no_ext = os.path.splitext(temp_input_path)[0]
            temp_svg_path = os.path.abspath(f"{base_no_ext}.svg")

            # Verify file exists, is readable, and has content with absolute path
            if not os.path.exists(temp_input_path):
                raise Exception(f"Temporary input file was not created: {temp_input_path}")
            
            if not os.path.isfile(temp_input_path):
                raise Exception(f"Path exists but is not a file: {temp_input_path}")
            
            if not os.access(temp_input_path, os.R_OK):
                raise Exception(f"File is not readable: {temp_input_path}")
            
            file_size = os.path.getsize(temp_input_path)
            if file_size == 0:
                raise Exception(f"Temporary input file is empty: {temp_input_path}")
            
            if file_size != len(image_bytes):
                raise Exception(f"File size mismatch: expected {len(image_bytes)}, got {file_size}")

            # Get original image dimensions - verify image is valid
            try:
                img = Image.open(temp_input_path)
                img.verify()  # Verify it's a valid image
                img = Image.open(temp_input_path)  # Reopen after verify
                original_width, original_height = img.size
                img.close()
            except Exception as e:
                raise Exception(f"Invalid image file: {str(e)}")

            # Final verification before calling vtracer - ensure file is still there
            if not os.path.exists(temp_input_path) or not os.access(temp_input_path, os.R_OK):
                raise Exception(f"File disappeared or became unreadable before vtracer call: {temp_input_path}")

            # Try to read file back to verify it's accessible (final sanity check)
            try:
                with open(temp_input_path, 'rb') as test_file:
                    test_bytes = test_file.read()
                    if len(test_bytes) != len(image_bytes):
                        raise Exception(f"File read verification failed: read {len(test_bytes)} bytes, expected {len(image_bytes)}")
            except Exception as e:
                raise Exception(f"Cannot read file back before vtracer: {str(e)}")

            # Convert using VTracer with absolute paths
            # Ensure paths are strings (not Path objects) for Rust compatibility
            vtracer_input_path = str(temp_input_path)
            vtracer_output_path = str(temp_svg_path)
            
            # Final debug check - log to stderr (more reliable than print in some environments)
            import sys
            sys.stderr.write(f"DEBUG: VTracer input path: {vtracer_input_path}\n")
            sys.stderr.write(f"DEBUG: VTracer output path: {vtracer_output_path}\n")
            sys.stderr.write(f"DEBUG: Input file exists: {os.path.exists(vtracer_input_path)}\n")
            sys.stderr.write(f"DEBUG: Input file readable: {os.access(vtracer_input_path, os.R_OK)}\n")
            sys.stderr.write(f"DEBUG: Input file size: {os.path.getsize(vtracer_input_path)}\n")
            sys.stderr.write(f"DEBUG: Input file permissions: {oct(os.stat(vtracer_input_path).st_mode)}\n")
            sys.stderr.write(f"DEBUG: Current working directory: {os.getcwd()}\n")
            sys.stderr.flush()
            
            # Try calling vtracer with the resolved path
            # If it still fails, wrap in try/except to get better error message
            try:
                vtracer.convert_image_to_svg_py(
                    vtracer_input_path,
                    vtracer_output_path,
                    colormode=colormode,
                    color_precision=color_precision,
                    filter_speckle=filter_speckle,
                    corner_threshold=corner_threshold,
                    length_threshold=length_threshold,
                    max_iterations=max_iterations,
                    splice_threshold=splice_threshold,
                    path_precision=path_precision
                )
            except Exception as vtracer_error:
                # Log detailed error information
                sys.stderr.write(f"DEBUG: VTracer error type: {type(vtracer_error).__name__}\n")
                sys.stderr.write(f"DEBUG: VTracer error message: {str(vtracer_error)}\n")
                sys.stderr.flush()
                raise Exception(f"VTracer failed with path '{vtracer_input_path}': {str(vtracer_error)}")

            # Read SVG content
            with open(temp_svg_path, 'r') as f:
                svg_content = f.read()

            # Normalize SVG dimensions for consistent scaling
            svg_content = self.normalize_svg_dimensions(svg_content, original_width, original_height)

            # Cleanup
            for path in [temp_input_path, temp_svg_path]:
                if path and os.path.exists(path):
                    try:
                        os.unlink(path)
                    except:
                        pass  # Ignore cleanup errors

            return svg_content

        except Exception as e:
            # Ensure cleanup on error
            for path in [temp_input_path, temp_svg_path]:
                if path and os.path.exists(path):
                    try:
                        os.unlink(path)
                    except:
                        pass  # Ignore cleanup errors
            raise Exception(f"VTracer processing failed: {str(e)}")

vectorizer = VectorizerService()

@app.get("/health")
async def health_check():
    """Health check endpoint to verify server is awake and responsive"""
    return {"status": "healthy", "message": "Vectorizer service is running"}

@app.options("/vectorize")
async def vectorize_options():
    """Handle preflight OPTIONS request for /vectorize"""
    from fastapi.responses import Response
    return Response(status_code=200)

@app.post("/vectorize")
async def vectorize_image(file: UploadFile = File(...), parameters: str = Form("{}"), selected_method: str = Form("")):
    """Vectorize an uploaded image using multiple methods with parameters"""
    try:
        # Check file size (20MB limit)
        MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB in bytes
        file_bytes = await file.read()

        if len(file_bytes) > MAX_FILE_SIZE:
            raise HTTPException(status_code=400, detail="File size exceeds 20MB limit")

        # Restrict to specific image types
        allowed_types = ['image/png', 'image/jpeg', 'image/gif']
        if not file.content_type or file.content_type not in allowed_types:
            raise HTTPException(status_code=400, detail="File must be PNG, JPEG, or GIF format")

        # Parse parameters
        import json
        try:
            params = json.loads(parameters) if parameters != "{}" else {}
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=400, detail=f"Invalid JSON in parameters: {str(e)}")

        # Validate parameters
        if 'potrace' in params:
            validate_potrace_params(params['potrace'])
        if 'vtracer' in params:
            validate_vtracer_params(params['vtracer'])

        image_bytes = file_bytes

        # Process with all available methods (or just the selected one if specified)
        results = {}

        # If a specific method is requested, only process that one
        if selected_method and selected_method in ['potrace', 'vtracer']:
            if selected_method == 'potrace':
                try:
                    potrace_params = params.get('potrace', {})
                    results['potrace'] = await vectorizer.potrace_vectorize(image_bytes, **potrace_params)
                except Exception as e:
                    results['potrace'] = f"Error: {str(e)}"
            elif selected_method == 'vtracer':
                try:
                    vtracer_params = params.get('vtracer', {})
                    results['vtracer'] = await vectorizer.vtracer_vectorize(image_bytes, **vtracer_params)
                except Exception as e:
                    results['vtracer'] = f"Error: {str(e)}"
        else:
            # Process both methods (default behavior)
            # Process Potrace vectorization
            try:
                potrace_params = params.get('potrace', {})
                results['potrace'] = await vectorizer.potrace_vectorize(image_bytes, **potrace_params)
            except Exception as e:
                results['potrace'] = f"Error: {str(e)}"

            # Process VTracer vectorization
            try:
                vtracer_params = params.get('vtracer', {})
                results['vtracer'] = await vectorizer.vtracer_vectorize(image_bytes, **vtracer_params)
            except Exception as e:
                results['vtracer'] = f"Error: {str(e)}"

        # Convert original image to base64 for display
        original_b64 = base64.b64encode(image_bytes).decode('utf-8')

        return JSONResponse({
            'success': True,
            'original_image': f"data:{file.content_type};base64,{original_b64}",
            'vectorized': results,
            'parameters_used': params
        })

    except HTTPException:
        # Re-raise HTTP exceptions (they already have proper status codes)
        raise
    except ParameterValidationError as e:
        raise HTTPException(status_code=400, detail=f"Parameter validation failed: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Processing failed: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)