# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

### Backend (Python/FastAPI)
```bash
cd backend

# Setup virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate   # Windows

# Install dependencies
pip install -r requirements.txt
pip install -r requirements-test.txt  # For testing
pip install vtracer  # Advanced vectorization

# Run backend server
python main.py  # Runs on http://localhost:8000

# Run tests
python run_tests.py
# Or pytest directly:
pytest tests/ -v
pytest tests/ --cov=main --cov-report=html

# Test specific functionality
python test_vtracer_integration.py
```

### Frontend (Vue.js)
```bash
cd frontend

# Install dependencies
npm install

# Run development server
npm run dev  # Runs on http://localhost:5173

# Build for production
npm run build

# Run tests
node run_tests.js
# Or npm scripts:
npm run test         # Interactive mode
npm run test:run     # Single run
npm run test:coverage
```

### Full Application
```bash
# Run both backend and frontend (from root directory)
./run_all_tests.sh

# Backend: http://localhost:8000
# Frontend: http://localhost:5173
```

## Architecture Overview

### Core System Design
This is a **dual-method image vectorization web application** with a **FastAPI backend** and **Vue.js frontend**. The system processes raster images (PNG) and converts them to SVG using two vectorization methods:

1. **Potrace**: Traditional black & white vectorization with parameter controls
2. **VTracer**: Advanced color-preserving vectorization (Rust-based with Python bindings)

### Key Architectural Patterns

#### Backend (`backend/main.py`)
- **VectorizerService class**: Centralizes vectorization logic with method-specific processors
- **Selective processing**: API can process all methods or just the selected method (`selected_method` parameter) for performance optimization during parameter adjustments
- **SVG normalization**: `normalize_svg_dimensions()` ensures consistent scaling between Potrace (points) and VTracer (pixels) outputs
- **Dual method responses**: Always returns both vectorization results unless `selected_method` is specified

#### Frontend (`frontend/src/App.vue`)
- **Single-page application**: All functionality in one Vue component using Composition API
- **Dual-pane comparison interface**: Original image on left, vectorized on right with draggable slider
- **Method-specific parameter panels**: Dynamic UI that shows/hides controls based on selected vectorization method
- **Debounced parameter updates**: `debouncedReprocess()` prevents excessive API calls during parameter adjustment
- **Result merging**: When processing single methods, preserves existing results for the other method

### Critical Implementation Details

#### Parameter System
The parameter system has specific patterns that must be maintained:

```javascript
// Frontend parameter structure
parameters: {
  potrace: {
    invert: false,
    turdsize: 2,
    turnpolicy: 'minority',
    alphamax: 1.0,
    opticurve: true
  },
  vtracer: {
    colormode: 'color',
    color_precision: 6,
    filter_speckle: 4,
    corner_threshold: 60,
    // ... other VTracer parameters
  }
}
```

```python
# Backend processing
if selected_method == 'potrace':
    potrace_params = params.get('potrace', {})
    results['potrace'] = await vectorizer.potrace_vectorize(image_bytes, **potrace_params)
# Only processes the selected method when specified
```

#### SVG Scale Normalization
A critical feature that prevents ~2x scale differences between methods:

```python
def normalize_svg_dimensions(self, svg_content: str, original_width: int, original_height: int) -> str:
    # Converts Potrace pt units to px
    # Adds viewBox attributes for consistent scaling
    # Maintains original image aspect ratio
```

#### Result Merging Pattern
Frontend merges results to preserve non-active method results:

```javascript
if (selectedMethodOnly && this.results) {
  this.results = {
    ...this.results,
    vectorized: {
      ...this.results.vectorized,
      ...response.data.vectorized
    }
  }
}
```

### System Dependencies

#### Backend Requirements
- **System**: `potrace` binary must be installed (`brew install potrace` on macOS)
- **Python**: VTracer requires Rust-based bindings (`pip install vtracer`)
- **Image Processing**: PIL/Pillow for image manipulation and format conversion

#### Frontend Requirements
- **Node.js**: 16+ required for Vite and Vue 3
- **Axios**: HTTP client configured for multipart/form-data uploads
- **Development**: Vite for hot module replacement during development

### Testing Architecture

#### Backend Testing Strategy
- **Unit tests**: Individual vectorization method testing
- **Integration tests**: Full API endpoint testing with real HTTP requests
- **Parameter validation tests**: Ensures parameter passing works correctly
- **Performance benchmarks**: Timing and memory efficiency validation

#### Frontend Testing Strategy
- **Component tests**: Vue.js component behavior using @vue/test-utils
- **Integration tests**: Full user workflows with MSW (Mock Service Worker)
- **Parameter system tests**: UI control validation and API integration

### Configuration Notes

#### CORS Configuration
Backend allows specific origins for frontend development:
```python
allow_origins=["http://localhost:3000", "http://localhost:5173"]
```

#### File Upload Handling
- Backend: Handles multipart/form-data with parameters as JSON string
- Frontend: Uses FormData with separate file and parameters fields
- Max file size and type validation handled by FastAPI

#### Development vs Production
- Debug logging can be enabled/disabled in `main.py`
- Frontend builds to static files via `npm run build`
- Backend runs with uvicorn for both development and production

### Common Issues and Solutions

#### VTracer Installation
If `pip install vtracer` fails, VTracer functionality will error gracefully. The Rust toolchain may be required for compilation on some systems.

#### Scale Consistency
The SVG normalization system ensures both methods produce consistently-scaled output. If scale differences appear, check the `normalize_svg_dimensions` function.

#### Parameter Updates
Parameter changes trigger debounced API calls with `selected_method` to avoid reprocessing both methods unnecessarily. The 800ms debounce prevents excessive calls during slider adjustments.