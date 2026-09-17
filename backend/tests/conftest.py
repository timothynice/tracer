"""Test configuration and fixtures."""
import asyncio
import io
import os
import tempfile
from typing import Generator, Any
import pytest
import numpy as np
from PIL import Image
from fastapi.testclient import TestClient
from unittest.mock import Mock, patch
from main import app, VectorizerService


@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for the entire test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def client() -> TestClient:
    """Create a test client for FastAPI app."""
    return TestClient(app)


@pytest.fixture
def vectorizer_service() -> VectorizerService:
    """Create a VectorizerService instance for testing."""
    return VectorizerService()


@pytest.fixture
def sample_image_bytes() -> bytes:
    """Create sample image bytes for testing."""
    # Create a simple black square on white background
    image = Image.new('RGB', (100, 100), 'white')
    # Add a black rectangle in the middle
    pixels = image.load()
    for i in range(25, 75):
        for j in range(25, 75):
            pixels[i, j] = (0, 0, 0)

    # Convert to bytes
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()


@pytest.fixture
def complex_sample_image_bytes() -> bytes:
    """Create a more complex sample image for comprehensive testing."""
    # Create image with multiple shapes and text-like patterns
    image = Image.new('RGB', (200, 200), 'white')
    pixels = image.load()

    # Black square
    for i in range(20, 60):
        for j in range(20, 60):
            pixels[i, j] = (0, 0, 0)

    # Black circle approximation
    center_x, center_y = 150, 150
    radius = 25
    for i in range(center_x - radius, center_x + radius):
        for j in range(center_y - radius, center_y + radius):
            if 0 <= i < 200 and 0 <= j < 200:
                if (i - center_x)**2 + (j - center_y)**2 <= radius**2:
                    pixels[i, j] = (0, 0, 0)

    # Horizontal line
    for i in range(50, 150):
        if 0 <= i < 200:
            pixels[i, 100] = (0, 0, 0)

    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()


@pytest.fixture
def empty_image_bytes() -> bytes:
    """Create an empty/white image for testing edge cases."""
    image = Image.new('RGB', (50, 50), 'white')
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()


@pytest.fixture
def text_like_image_bytes() -> bytes:
    """Create an image that looks like text for testing inversion."""
    image = Image.new('RGB', (150, 50), 'black')  # Black background
    pixels = image.load()

    # Add white text-like rectangles (simulating white text on black background)
    # Letter "H"
    for i in range(10, 30):
        pixels[i, 10] = (255, 255, 255)  # Left vertical line
        pixels[i, 25] = (255, 255, 255)  # Right vertical line
    for j in range(10, 26):
        pixels[20, j] = (255, 255, 255)  # Horizontal line

    # Letter "I"
    for j in range(35, 45):
        pixels[10, j] = (255, 255, 255)  # Top horizontal line
        pixels[30, j] = (255, 255, 255)  # Bottom horizontal line
    for i in range(10, 31):
        pixels[i, 40] = (255, 255, 255)  # Vertical line

    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()


@pytest.fixture
def mock_potrace_success():
    """Mock successful potrace execution."""
    with patch('subprocess.run') as mock_run:
        # Mock successful potrace execution
        mock_run.return_value = Mock(
            returncode=0,
            stderr="",
            stdout=""
        )

        # Mock file operations
        with patch('builtins.open', create=True) as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">\n'
                '<path d="M10,10 L90,10 L90,90 L10,90 Z" fill="black"/>\n'
                '</svg>'
            )

            with patch('os.path.exists', return_value=True):
                with patch('os.unlink'):
                    yield mock_run


@pytest.fixture
def mock_potrace_failure():
    """Mock failed potrace execution."""
    with patch('subprocess.run') as mock_run:
        mock_run.return_value = Mock(
            returncode=1,
            stderr="potrace: error processing file",
            stdout=""
        )
        yield mock_run


@pytest.fixture
def sample_parameters() -> dict:
    """Sample parameters for testing."""
    return {
        "potrace": {
            "invert": False,
            "turdsize": 2,
            "turnpolicy": "minority",
            "alphamax": 1.0,
            "opticurve": True
        },
        "opencv_edge": {
            "blur_size": 5,
            "low_threshold": 30,
            "high_threshold": 100,
            "min_area": 50,
            "epsilon_factor": 0.02,
            "stroke_width": 2
        },
        "opencv_contour": {
            "threshold": 127,
            "min_area": 100,
            "epsilon_factor": 0.01,
            "invert_threshold": False
        },
        "opencv": {
            "low_threshold": 50,
            "high_threshold": 150,
            "min_contour_points": 3
        }
    }


@pytest.fixture
def invalid_image_bytes() -> bytes:
    """Create invalid image bytes for error testing."""
    return b"This is not an image file"


@pytest.fixture
def performance_test_image() -> bytes:
    """Create a larger, more complex image for performance testing."""
    image = Image.new('RGB', (1000, 1000), 'white')
    pixels = image.load()

    # Create a complex pattern with multiple shapes
    for i in range(0, 1000, 50):
        for j in range(0, 1000, 50):
            # Create alternating squares
            if (i + j) % 100 == 0:
                for x in range(i, min(i + 25, 1000)):
                    for y in range(j, min(j + 25, 1000)):
                        pixels[x, y] = (0, 0, 0)

    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()


@pytest.fixture
def mock_opencv_error():
    """Mock OpenCV errors for testing error handling."""
    with patch('cv2.imdecode', side_effect=Exception("OpenCV processing error")):
        yield


@pytest.fixture
def temp_directory() -> Generator[str, None, None]:
    """Create a temporary directory for file operations."""
    with tempfile.TemporaryDirectory() as temp_dir:
        yield temp_dir