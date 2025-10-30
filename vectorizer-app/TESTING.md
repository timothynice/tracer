# Vectorizer Application Test Suite

This document describes the comprehensive test suite for the vectorizer application, covering both backend (Python/FastAPI) and frontend (Vue.js) components.

## Overview

The test suite validates:
- **Backend API functionality** with parameter passing and error handling
- **Frontend user interactions** including file upload and parameter controls
- **Integration between components** ensuring the system works end-to-end
- **Performance characteristics** of vectorization methods
- **Parameter system reliability** which has been a focus area

## Backend Testing (Python/FastAPI)

### Prerequisites

1. **Install test dependencies:**
   ```bash
   cd backend
   pip install -r requirements-test.txt
   ```

2. **Ensure potrace is installed** (for potrace-specific tests):
   ```bash
   # On macOS
   brew install potrace

   # On Ubuntu/Debian
   sudo apt-get install potrace

   # On Windows
   # Download from http://potrace.sourceforge.net/
   ```

### Test Structure

```
backend/tests/
├── conftest.py                 # Test configuration and fixtures
├── test_vectorizer_service.py  # Unit tests for vectorization methods
├── test_api_endpoints.py       # Integration tests for FastAPI endpoints
├── test_performance.py         # Performance and benchmark tests
└── test_parameter_system.py    # Focused tests for parameter handling
```

### Running Backend Tests

```bash
cd backend

# Run all tests
python run_tests.py

# Or use pytest directly
pytest tests/ -v

# Run specific test categories
pytest tests/ -m unit           # Unit tests only
pytest tests/ -m integration    # Integration tests only
pytest tests/ -m performance    # Performance tests only

# Run with coverage
pytest tests/ --cov=main --cov-report=html
```

### Test Categories

#### Unit Tests (`test_vectorizer_service.py`)
- Tests each vectorization method individually
- Validates parameter handling for each method
- Tests error conditions and edge cases
- Covers boundary value testing

#### Integration Tests (`test_api_endpoints.py`)
- Tests FastAPI endpoints with real HTTP requests
- Validates file upload functionality
- Tests parameter passing through the API
- Covers CORS and error response handling

#### Parameter System Tests (`test_parameter_system.py`)
- Focused testing of the parameter system
- Tests parameter parsing and validation
- Tests parameter type conversion
- Tests default parameter handling
- Validates parameter isolation between methods

#### Performance Tests (`test_performance.py`)
- Benchmarks vectorization method performance
- Tests concurrent processing capabilities
- Validates performance with different image sizes
- Tests memory efficiency

### Key Test Fixtures

- `sample_image_bytes`: Basic test image (black square on white background)
- `complex_sample_image_bytes`: More complex test image with multiple shapes
- `text_like_image_bytes`: Image simulating white text on black background
- `performance_test_image`: Large image for performance testing
- `mock_potrace_success/failure`: Mocked potrace execution

## Frontend Testing (Vue.js/Vitest)

### Prerequisites

1. **Install test dependencies:**
   ```bash
   cd frontend
   npm install
   ```

### Test Structure

```
frontend/src/test/
├── setup.js           # Test configuration and mocks
├── App.test.js        # Vue component unit tests
└── integration.test.js # Frontend integration tests
```

### Running Frontend Tests

```bash
cd frontend

# Run all tests
node run_tests.js

# Or use npm scripts
npm run test           # Interactive mode
npm run test:run       # Single run
npm run test:ui        # UI mode
npm run test:coverage  # With coverage
```

### Test Categories

#### Component Unit Tests (`App.test.js`)
- Tests Vue component functionality
- Validates user interface interactions
- Tests parameter controls and validation
- Covers file upload handling
- Tests method switching logic

#### Integration Tests (`integration.test.js`)
- Tests complete user workflows
- Validates API integration with mocked backend
- Tests error handling and recovery
- Covers performance and user experience

### Mock Setup

The test suite uses MSW (Mock Service Worker) to mock API calls:
- Simulates successful vectorization responses
- Tests error conditions
- Provides consistent test data

## Parameter System Testing

Given the focus on parameter system issues, here are specific test scenarios:

### Parameter Validation Tests
```python
# Test that parameters are correctly parsed and typed
def test_parameter_types_validation(client, sample_image_bytes):
    params = {
        "opencv_edge": {
            "blur_size": 7,        # int
            "low_threshold": 25.5,  # float
            "stroke_width": 3       # int
        }
    }
    # Validates that these types are preserved through the API
```

### Parameter Default Handling
```python
# Test that missing parameters use appropriate defaults
def test_parameter_defaults_when_missing(client, sample_image_bytes):
    params = {"potrace": {"invert": True}}  # Only one parameter provided
    # Should succeed using defaults for missing parameters
```

### Parameter Method Isolation
```python
# Test that each method only uses its own parameters
def test_selected_method_parameter_isolation(client, sample_image_bytes):
    mixed_params = {
        "potrace": {"invert": True},
        "opencv_edge": {"blur_size": 9}
    }
    # When "potrace" is selected, should only use potrace parameters
```

## Running All Tests

### Backend and Frontend Together

```bash
# From the root directory
./run_all_tests.sh
```

Or manually:

```bash
# Backend tests
cd backend
python run_tests.py

# Frontend tests
cd ../frontend
node run_tests.js
```

## Test Configuration

### Backend Configuration (`pytest.ini`)
- Coverage threshold: 85%
- Test markers for categorization
- Custom test discovery patterns
- Coverage reporting configuration

### Frontend Configuration (`vitest.config.js`)
- Happy DOM environment for Vue testing
- MSW integration for API mocking
- Coverage reporting with v8
- CSS mocking for component tests

## Continuous Integration

The test suite is designed to work in CI environments:

### GitHub Actions Example
```yaml
name: Test Suite
on: [push, pull_request]
jobs:
  backend-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - uses: actions/setup-python@v2
        with:
          python-version: '3.9'
      - run: |
          cd backend
          pip install -r requirements.txt -r requirements-test.txt
          python run_tests.py

  frontend-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - uses: actions/setup-node@v2
        with:
          node-version: '16'
      - run: |
          cd frontend
          npm install
          node run_tests.js
```

## Troubleshooting

### Common Issues

#### Backend Tests
1. **Potrace not found**: Install potrace system dependency
2. **OpenCV issues**: Ensure opencv-python is properly installed
3. **Permission errors**: Check file permissions in temp directories

#### Frontend Tests
1. **Node version**: Ensure Node.js 16+ is installed
2. **Dependencies**: Run `npm install` if node_modules is missing
3. **MSW issues**: Clear browser cache if service worker conflicts occur

### Debug Mode

#### Backend
```bash
pytest tests/ -v -s --tb=long --log-cli-level=DEBUG
```

#### Frontend
```bash
npx vitest --ui  # Opens browser-based test UI
npm run test -- --reporter=verbose
```

## Performance Benchmarks

The test suite includes performance benchmarks to ensure vectorization methods meet performance requirements:

- **Individual method timing**: Each method should complete within 5 seconds for standard images
- **Concurrent processing**: 5 concurrent requests should complete within 15 seconds
- **Memory efficiency**: Tests validate memory usage doesn't grow excessively
- **Parameter impact**: Tests measure how parameter changes affect performance

## Coverage Goals

- **Backend**: Minimum 85% code coverage
- **Frontend**: Minimum 80% component coverage
- **Integration**: All critical user paths covered
- **Error scenarios**: All error conditions tested

## Contributing

When adding new features:

1. **Add unit tests** for new methods or components
2. **Add integration tests** for new API endpoints or user flows
3. **Update parameter tests** if adding new parameters
4. **Add performance tests** for computationally intensive features
5. **Update this documentation** with any new test categories or setup requirements

The test suite is designed to catch regressions early and ensure the parameter system works reliably across all vectorization methods.