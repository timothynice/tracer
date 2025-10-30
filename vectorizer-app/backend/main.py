from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from PIL import Image
import io
import base64
import subprocess
import os
import tempfile
import vtracer
import re

app = FastAPI(title="Image Vectorizer API")

# Configure CORS from environment
allowed_origins_env = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:5173")
allowed_origins = [o.strip() for o in allowed_origins_env.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
        """Normalize SVG dimensions to consistent pixel units with proper viewBox"""
        try:
            # For Potrace SVGs: Convert from points to pixels and add viewBox if missing
            if 'pt"' in svg_content:
                # Replace pt units with px and scale appropriately
                # 1 pt = 1.33 px approximately, but we want 1:1 mapping to original image
                svg_content = re.sub(r'width="([0-9.]+)pt"', f'width="{original_width}"', svg_content)
                svg_content = re.sub(r'height="([0-9.]+)pt"', f'height="{original_height}"', svg_content)

                # Ensure viewBox matches original dimensions
                viewbox_pattern = r'viewBox="[^"]*"'
                new_viewbox = f'viewBox="0 0 {original_width} {original_height}"'
                if 'viewBox=' in svg_content:
                    svg_content = re.sub(viewbox_pattern, new_viewbox, svg_content)
                else:
                    # Add viewBox after the opening svg tag
                    svg_content = re.sub(r'(<svg[^>]*)', f'\\1 {new_viewbox}', svg_content)

            # For VTracer SVGs: Ensure viewBox is present for consistent scaling
            elif not 'viewBox=' in svg_content:
                # Add viewBox to VTracer SVGs for consistency
                viewbox = f'viewBox="0 0 {original_width} {original_height}"'
                svg_content = re.sub(r'(<svg[^>]*)', f'\\1 {viewbox}', svg_content)

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

            # Optional debug logging (comment out for production)
            print(f"DEBUG: Potrace command: {' '.join(cmd)}")
            print(f"DEBUG: Potrace params - invert:{invert}, turdsize:{turdsize}, turnpolicy:{turnpolicy}, alphamax:{alphamax}, opticurve:{opticurve}")

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
        try:
            # Create temporary files
            with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as temp_input:
                temp_input.write(image_bytes)
                temp_input_path = temp_input.name

            temp_svg_path = temp_input_path.replace('.png', '.svg')

            # Get original image dimensions
            img = Image.open(temp_input_path)
            original_width, original_height = img.size

            # Convert using VTracer
            vtracer.convert_image_to_svg_py(
                temp_input_path,
                temp_svg_path,
                colormode=colormode,
                color_precision=color_precision,
                filter_speckle=filter_speckle,
                corner_threshold=corner_threshold,
                length_threshold=length_threshold,
                max_iterations=max_iterations,
                splice_threshold=splice_threshold,
                path_precision=path_precision
            )

            # Read SVG content
            with open(temp_svg_path, 'r') as f:
                svg_content = f.read()

            # Normalize SVG dimensions for consistent scaling
            svg_content = self.normalize_svg_dimensions(svg_content, original_width, original_height)

            # Cleanup
            for path in [temp_input_path, temp_svg_path]:
                if os.path.exists(path):
                    os.unlink(path)

            return svg_content

        except Exception as e:
            raise Exception(f"VTracer processing failed: {str(e)}")

vectorizer = VectorizerService()

@app.post("/vectorize")
async def vectorize_image(file: UploadFile = File(...), parameters: str = Form("{}"), selected_method: str = Form("")):
    """Vectorize an uploaded image using multiple methods with parameters"""
    if not file.content_type or not file.content_type.startswith('image/'):
        raise HTTPException(status_code=400, detail="File must be an image")

    # Parse parameters OUTSIDE the main try block so validation errors aren't caught
    try:
        import json
        params = json.loads(parameters) if parameters != "{}" else {}
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON in parameters: {str(e)}")

    # Validate parameters OUTSIDE the main try block
    try:
        if 'potrace' in params:
            validate_potrace_params(params['potrace'])
        if 'vtracer' in params:
            validate_vtracer_params(params['vtracer'])
    except ParameterValidationError as e:
        raise HTTPException(status_code=400, detail=f"Parameter validation failed: {str(e)}")

    try:
        # Optional debug logging (comment out for production)
        print(f"DEBUG: Received parameters: {params}")
        print(f"DEBUG: Selected method: '{selected_method}' (type: {type(selected_method)})")
        print(f"DEBUG: selected_method == 'vtracer': {selected_method == 'vtracer'}")
        print(f"DEBUG: selected_method == 'potrace': {selected_method == 'potrace'}")
        print(f"DEBUG: bool(selected_method): {bool(selected_method)}")
        print(f"DEBUG: selected_method in ['potrace', 'vtracer']: {selected_method in ['potrace', 'vtracer']}")

        # Read image bytes
        image_bytes = await file.read()

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
                print("DEBUG: Processing VTracer method!")
                try:
                    vtracer_params = params.get('vtracer', {})
                    print(f"DEBUG: VTracer params: {vtracer_params}")
                    results['vtracer'] = await vectorizer.vtracer_vectorize(image_bytes, **vtracer_params)
                    print("DEBUG: VTracer processing completed successfully")
                except Exception as e:
                    print(f"DEBUG: VTracer processing failed: {str(e)}")
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

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Processing failed: {str(e)}")

@app.get("/health")
async def health_check():
    return {"status": "healthy", "message": "Image Vectorizer API is running"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)