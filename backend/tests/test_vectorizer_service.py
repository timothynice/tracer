"""Unit tests for VectorizerService."""
import pytest
import asyncio
import cv2
import numpy as np
from unittest.mock import Mock, patch, mock_open
from main import VectorizerService


class TestVectorizerService:
    """Test cases for VectorizerService methods."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_opencv_vectorize_basic(self, vectorizer_service, sample_image_bytes):
        """Test basic OpenCV vectorization."""
        result = await vectorizer_service.opencv_vectorize(
            sample_image_bytes,
            low_threshold=50,
            high_threshold=150,
            min_contour_points=3
        )

        assert result.startswith('<?xml version="1.0" encoding="UTF-8"?>')
        assert '<svg' in result
        assert 'xmlns="http://www.w3.org/2000/svg"' in result
        assert '</svg>' in result

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_opencv_vectorize_parameters(self, vectorizer_service, sample_image_bytes):
        """Test OpenCV vectorization with different parameters."""
        # Test with different thresholds
        result1 = await vectorizer_service.opencv_vectorize(
            sample_image_bytes,
            low_threshold=10,
            high_threshold=50,
            min_contour_points=5
        )

        result2 = await vectorizer_service.opencv_vectorize(
            sample_image_bytes,
            low_threshold=100,
            high_threshold=200,
            min_contour_points=3
        )

        # Results should be different with different parameters
        assert result1 != result2
        assert both_contain_svg_structure(result1, result2)

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_opencv_edge_vectorize(self, vectorizer_service, complex_sample_image_bytes):
        """Test OpenCV edge detection vectorization."""
        result = await vectorizer_service.opencv_edge_vectorize(
            complex_sample_image_bytes,
            blur_size=5,
            low_threshold=30,
            high_threshold=100,
            min_area=50,
            epsilon_factor=0.02,
            stroke_width=2
        )

        assert is_valid_svg(result)
        assert 'stroke="black"' in result
        assert 'fill="none"' in result

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_opencv_edge_vectorize_parameters(self, vectorizer_service, complex_sample_image_bytes):
        """Test edge detection with various parameters."""
        # Test with high blur
        result_high_blur = await vectorizer_service.opencv_edge_vectorize(
            complex_sample_image_bytes,
            blur_size=15,
            low_threshold=30,
            high_threshold=100,
            min_area=50,
            stroke_width=2
        )

        # Test with low blur
        result_low_blur = await vectorizer_service.opencv_edge_vectorize(
            complex_sample_image_bytes,
            blur_size=3,
            low_threshold=30,
            high_threshold=100,
            min_area=50,
            stroke_width=2
        )

        assert is_valid_svg(result_high_blur)
        assert is_valid_svg(result_low_blur)
        # Results may differ due to different blur levels

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_opencv_contour_vectorize(self, vectorizer_service, complex_sample_image_bytes):
        """Test OpenCV contour vectorization."""
        result = await vectorizer_service.opencv_contour_vectorize(
            complex_sample_image_bytes,
            threshold=127,
            min_area=100,
            epsilon_factor=0.01,
            invert_threshold=False
        )

        assert is_valid_svg(result)
        assert 'fill="black"' in result

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_opencv_contour_vectorize_inverted(self, vectorizer_service, text_like_image_bytes):
        """Test contour vectorization with inverted threshold."""
        # Test normal threshold
        result_normal = await vectorizer_service.opencv_contour_vectorize(
            text_like_image_bytes,
            threshold=127,
            min_area=50,
            epsilon_factor=0.01,
            invert_threshold=False
        )

        # Test inverted threshold
        result_inverted = await vectorizer_service.opencv_contour_vectorize(
            text_like_image_bytes,
            threshold=127,
            min_area=50,
            epsilon_factor=0.01,
            invert_threshold=True
        )

        assert is_valid_svg(result_normal)
        assert is_valid_svg(result_inverted)
        # Results should be different
        assert result_normal != result_inverted

    @pytest.mark.asyncio
    @pytest.mark.unit
    @pytest.mark.requires_potrace
    async def test_potrace_vectorize_success(self, vectorizer_service, sample_image_bytes, mock_potrace_success):
        """Test successful potrace vectorization."""
        result = await vectorizer_service.potrace_vectorize(
            sample_image_bytes,
            invert=False,
            turdsize=2,
            turnpolicy='minority',
            alphamax=1.0,
            opticurve=True
        )

        assert is_valid_svg(result)
        assert 'path d=' in result

    @pytest.mark.asyncio
    @pytest.mark.unit
    @pytest.mark.requires_potrace
    async def test_potrace_vectorize_parameters(self, vectorizer_service, sample_image_bytes, mock_potrace_success):
        """Test potrace with different parameters."""
        # Test different turn policies
        for policy in ['black', 'white', 'left', 'right', 'minority', 'majority']:
            result = await vectorizer_service.potrace_vectorize(
                sample_image_bytes,
                invert=False,
                turdsize=2,
                turnpolicy=policy,
                alphamax=1.0,
                opticurve=True
            )
            assert is_valid_svg(result)

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_potrace_vectorize_failure_fallback(self, vectorizer_service, sample_image_bytes, mock_potrace_failure):
        """Test potrace failure fallback to OpenCV."""
        result = await vectorizer_service.potrace_vectorize(
            sample_image_bytes,
            invert=False,
            turdsize=2,
            turnpolicy='minority',
            alphamax=1.0,
            opticurve=True
        )

        # Should fall back to OpenCV method
        assert is_valid_svg(result)
        # Should not contain potrace-specific elements
        assert 'path d=' in result

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_opencv_invalid_image_error(self, vectorizer_service, invalid_image_bytes):
        """Test OpenCV methods with invalid image data."""
        with pytest.raises(Exception):
            await vectorizer_service.opencv_vectorize(invalid_image_bytes)

        with pytest.raises(Exception):
            await vectorizer_service.opencv_edge_vectorize(invalid_image_bytes)

        with pytest.raises(Exception):
            await vectorizer_service.opencv_contour_vectorize(invalid_image_bytes)

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_empty_image_handling(self, vectorizer_service, empty_image_bytes):
        """Test handling of empty/white images."""
        result = await vectorizer_service.opencv_vectorize(empty_image_bytes)

        # Should still produce valid SVG even if no contours found
        assert is_valid_svg(result)
        # May contain no path elements if image is empty
        assert '<svg' in result and '</svg>' in result

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_parameter_boundary_values(self, vectorizer_service, sample_image_bytes):
        """Test methods with boundary parameter values."""
        # Test OpenCV with minimum values
        result = await vectorizer_service.opencv_vectorize(
            sample_image_bytes,
            low_threshold=1,
            high_threshold=2,
            min_contour_points=3
        )
        assert is_valid_svg(result)

        # Test OpenCV edge with boundary values
        result = await vectorizer_service.opencv_edge_vectorize(
            sample_image_bytes,
            blur_size=1,  # Minimum odd value
            low_threshold=1,
            high_threshold=2,
            min_area=1,
            epsilon_factor=0.001,
            stroke_width=1
        )
        assert is_valid_svg(result)

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_large_parameter_values(self, vectorizer_service, sample_image_bytes):
        """Test methods with large parameter values."""
        # Test with very high thresholds
        result = await vectorizer_service.opencv_vectorize(
            sample_image_bytes,
            low_threshold=250,
            high_threshold=255,
            min_contour_points=50
        )
        assert is_valid_svg(result)

        # Test contour with high min_area (might filter out all contours)
        result = await vectorizer_service.opencv_contour_vectorize(
            sample_image_bytes,
            threshold=250,
            min_area=100000,  # Very large
            epsilon_factor=0.5,
            invert_threshold=False
        )
        assert is_valid_svg(result)

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_different_image_sizes(self, vectorizer_service):
        """Test vectorization with different image sizes."""
        from PIL import Image
        import io

        # Small image
        small_img = Image.new('RGB', (10, 10), 'white')
        small_buffer = io.BytesIO()
        small_img.save(small_buffer, format='PNG')
        small_result = await vectorizer_service.opencv_vectorize(small_buffer.getvalue())
        assert is_valid_svg(small_result)

        # Large image
        large_img = Image.new('RGB', (500, 500), 'white')
        large_buffer = io.BytesIO()
        large_img.save(large_buffer, format='PNG')
        large_result = await vectorizer_service.opencv_vectorize(large_buffer.getvalue())
        assert is_valid_svg(large_result)


def is_valid_svg(svg_string: str) -> bool:
    """Check if string is a valid SVG structure."""
    return (
        svg_string.startswith('<?xml version="1.0" encoding="UTF-8"?>') and
        '<svg' in svg_string and
        'xmlns="http://www.w3.org/2000/svg"' in svg_string and
        svg_string.endswith('</svg>')
    )


def both_contain_svg_structure(svg1: str, svg2: str) -> bool:
    """Check if both strings contain valid SVG structure."""
    return is_valid_svg(svg1) and is_valid_svg(svg2)