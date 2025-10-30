# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Structure

This is a **dual-method image vectorization application** located in the `vectorizer-app/` directory. The repository root contains the main application and supporting files.

```
tracer/
├── vectorizer-app/          # Main application directory
│   ├── backend/             # Python/FastAPI backend
│   ├── frontend/            # Vue.js frontend
│   ├── CLAUDE.md            # Detailed project guidance
│   ├── TESTING.md           # Comprehensive test documentation
│   └── run_all_tests.sh     # Master test runner
└── CLAUDE.md               # This file (root level guidance)
```

## Development Commands

### Quick Start
```bash
# Navigate to the main application
cd vectorizer-app

# Follow the detailed commands in vectorizer-app/CLAUDE.md
# Or use the master test runner:
./run_all_tests.sh
```

### Backend (Python/FastAPI)
```bash
cd vectorizer-app/backend

# Setup and run
python -m venv venv
source venv/bin/activate  # Linux/Mac
pip install -r requirements.txt
python main.py  # Runs on http://localhost:8000

# Run tests
python run_tests.py
```

### Frontend (Vue.js/Vite)
```bash
cd vectorizer-app/frontend

# Setup and run
npm install
npm run dev  # Runs on http://localhost:5173

# Run tests
node run_tests.js
```

## Key Architecture Notes

### System Overview
- **Dual vectorization methods**: Potrace (traditional B&W) and VTracer (advanced color)
- **Parameter-driven**: Each method has specific parameters that can be adjusted in real-time
- **Debounced processing**: Parameter changes trigger selective reprocessing to avoid redundant work
- **SVG normalization**: Ensures consistent scaling between different vectorization methods

### Critical Implementation Patterns

#### Selective Method Processing
The system can process either all methods or just the selected method for performance optimization:
- Frontend sends `selected_method` parameter during parameter adjustments
- Backend only processes the specified method, preserving results for others
- Frontend merges results to maintain complete state

#### Parameter System
Each vectorization method has its own parameter namespace:
- `potrace`: Traditional parameters (invert, turdsize, turnpolicy, etc.)
- `vtracer`: Advanced parameters (colormode, color_precision, etc.)
- Parameters are validated and type-converted in the backend

### Development Requirements

#### System Dependencies
- **Backend**: `potrace` binary must be installed (`brew install potrace`)
- **Python packages**: VTracer requires Rust-based bindings (`pip install vtracer`)
- **Node.js**: Version 16+ required for Vite and Vue 3

#### Test Dependencies
- **Backend**: `requirements-test.txt` includes pytest, coverage tools
- **Frontend**: Vitest, @vue/test-utils, MSW for API mocking

## Testing Strategy

### Comprehensive Test Suite
The application has extensive testing documented in `vectorizer-app/TESTING.md`:

- **Backend**: Unit, integration, parameter validation, and performance tests
- **Frontend**: Component tests and integration tests with MSW
- **Parameter system**: Focused testing of parameter handling and validation
- **End-to-end**: Master test runner validates the complete system

### Running All Tests
```bash
cd vectorizer-app
./run_all_tests.sh  # Runs both backend and frontend test suites
```

## Important Files

- **`vectorizer-app/CLAUDE.md`**: Detailed development guidance for the main application
- **`vectorizer-app/TESTING.md`**: Comprehensive testing documentation and strategies
- **`vectorizer-app/run_all_tests.sh`**: Master test runner with detailed output
- **`vectorizer-app/backend/main.py`**: Core FastAPI application with VectorizerService
- **`vectorizer-app/frontend/src/App.vue`**: Single-page Vue application with dual-pane interface

## Performance Considerations

- **Debounced parameter updates**: 800ms delay prevents excessive API calls
- **Selective method processing**: Only reprocess the method being adjusted
- **SVG normalization**: Prevents scale inconsistencies between methods
- **Image processing**: Handles PNG uploads with size and format validation

## Common Development Tasks

When working on this codebase:

1. **Adding new vectorization parameters**: Update both frontend parameter UI and backend validation
2. **Modifying API endpoints**: Ensure CORS settings include development origins
3. **Testing parameter changes**: Use the comprehensive parameter test suites
4. **Performance optimization**: Check impact on both individual and concurrent processing
5. **Frontend changes**: Test with the debounced parameter system and result merging logic

Refer to `vectorizer-app/CLAUDE.md` for detailed implementation guidance and `vectorizer-app/TESTING.md` for comprehensive testing strategies.