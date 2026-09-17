"""Performance tests for vectorization methods."""
import pytest
import asyncio
import time
from main import VectorizerService


class TestVectorizationPerformance:
    """Performance tests for vectorization methods."""

    @pytest.mark.performance
    @pytest.mark.slow
    async def test_opencv_vectorize_performance(self, vectorizer_service, performance_test_image, benchmark):
        """Benchmark OpenCV vectorization performance."""
        async def vectorize():
            return await vectorizer_service.opencv_vectorize(
                performance_test_image,
                low_threshold=50,
                high_threshold=150,
                min_contour_points=3
            )

        result = await benchmark.pedantic(vectorize, rounds=3, iterations=1)
        assert result is not None
        assert '<svg' in result

    @pytest.mark.performance
    @pytest.mark.slow
    async def test_opencv_edge_vectorize_performance(self, vectorizer_service, performance_test_image, benchmark):
        """Benchmark OpenCV edge detection performance."""
        async def vectorize():
            return await vectorizer_service.opencv_edge_vectorize(
                performance_test_image,
                blur_size=5,
                low_threshold=30,
                high_threshold=100,
                min_area=50,
                epsilon_factor=0.02,
                stroke_width=2
            )

        result = await benchmark.pedantic(vectorize, rounds=3, iterations=1)
        assert result is not None
        assert '<svg' in result

    @pytest.mark.performance
    @pytest.mark.slow
    async def test_opencv_contour_vectorize_performance(self, vectorizer_service, performance_test_image, benchmark):
        """Benchmark OpenCV contour detection performance."""
        async def vectorize():
            return await vectorizer_service.opencv_contour_vectorize(
                performance_test_image,
                threshold=127,
                min_area=100,
                epsilon_factor=0.01,
                invert_threshold=False
            )

        result = await benchmark.pedantic(vectorize, rounds=3, iterations=1)
        assert result is not None
        assert '<svg' in result

    @pytest.mark.performance
    @pytest.mark.asyncio
    async def test_method_performance_comparison(self, vectorizer_service, complex_sample_image_bytes):
        """Compare performance of different vectorization methods."""
        methods = [
            ('opencv', {'low_threshold': 50, 'high_threshold': 150, 'min_contour_points': 3}),
            ('opencv_edge', {'blur_size': 5, 'low_threshold': 30, 'high_threshold': 100, 'min_area': 50, 'epsilon_factor': 0.02, 'stroke_width': 2}),
            ('opencv_contour', {'threshold': 127, 'min_area': 100, 'epsilon_factor': 0.01, 'invert_threshold': False})
        ]

        results = {}

        for method_name, params in methods:
            start_time = time.time()

            if method_name == 'opencv':
                await vectorizer_service.opencv_vectorize(complex_sample_image_bytes, **params)
            elif method_name == 'opencv_edge':
                await vectorizer_service.opencv_edge_vectorize(complex_sample_image_bytes, **params)
            elif method_name == 'opencv_contour':
                await vectorizer_service.opencv_contour_vectorize(complex_sample_image_bytes, **params)

            end_time = time.time()
            results[method_name] = end_time - start_time

        # Ensure all methods complete within reasonable time (5 seconds each)
        for method_name, duration in results.items():
            assert duration < 5.0, f"{method_name} took too long: {duration}s"

        # Log performance comparison (pytest will capture this)
        print(f"\nPerformance comparison:")
        for method_name, duration in sorted(results.items(), key=lambda x: x[1]):
            print(f"  {method_name}: {duration:.3f}s")

    @pytest.mark.performance
    @pytest.mark.asyncio
    async def test_concurrent_vectorization(self, vectorizer_service, sample_image_bytes):
        """Test concurrent vectorization performance."""
        async def single_vectorize():
            return await vectorizer_service.opencv_vectorize(sample_image_bytes)

        start_time = time.time()

        # Run 5 concurrent vectorizations
        tasks = [single_vectorize() for _ in range(5)]
        results = await asyncio.gather(*tasks)

        end_time = time.time()
        total_time = end_time - start_time

        # All results should be valid
        for result in results:
            assert '<svg' in result

        # Should complete within reasonable time
        assert total_time < 10.0, f"Concurrent vectorization took too long: {total_time}s"

        print(f"\nConcurrent vectorization of 5 images: {total_time:.3f}s")

    @pytest.mark.performance
    @pytest.mark.asyncio
    async def test_parameter_impact_on_performance(self, vectorizer_service, complex_sample_image_bytes):
        """Test how different parameters affect performance."""
        # Test edge detection with different blur sizes
        blur_sizes = [1, 5, 11, 21]
        times = {}

        for blur_size in blur_sizes:
            start_time = time.time()
            await vectorizer_service.opencv_edge_vectorize(
                complex_sample_image_bytes,
                blur_size=blur_size,
                low_threshold=30,
                high_threshold=100,
                min_area=50,
                epsilon_factor=0.02,
                stroke_width=2
            )
            end_time = time.time()
            times[blur_size] = end_time - start_time

        # Log performance impact
        print(f"\nBlur size performance impact:")
        for blur_size, duration in times.items():
            print(f"  Blur size {blur_size}: {duration:.3f}s")

        # Higher blur sizes might take slightly longer but should still be reasonable
        for blur_size, duration in times.items():
            assert duration < 3.0, f"Blur size {blur_size} took too long: {duration}s"

    @pytest.mark.performance
    @pytest.mark.asyncio
    async def test_memory_efficiency(self, vectorizer_service):
        """Test memory efficiency with multiple image sizes."""
        from PIL import Image
        import io
        import gc

        sizes = [(50, 50), (100, 100), (200, 200), (400, 400)]

        for width, height in sizes:
            # Create image of specific size
            img = Image.new('RGB', (width, height), 'white')
            # Add some content
            pixels = img.load()
            for i in range(10, min(width-10, 40)):
                for j in range(10, min(height-10, 40)):
                    pixels[i, j] = (0, 0, 0)

            buffer = io.BytesIO()
            img.save(buffer, format='PNG')
            image_bytes = buffer.getvalue()

            # Vectorize and measure
            start_time = time.time()
            result = await vectorizer_service.opencv_vectorize(image_bytes)
            end_time = time.time()

            assert '<svg' in result
            print(f"Image {width}x{height}: {end_time - start_time:.3f}s")

            # Force garbage collection
            gc.collect()

    @pytest.mark.performance
    @pytest.mark.asyncio
    async def test_error_handling_performance(self, vectorizer_service, invalid_image_bytes):
        """Test that error handling doesn't cause performance issues."""
        start_time = time.time()

        # Try to vectorize invalid data multiple times
        for _ in range(10):
            try:
                await vectorizer_service.opencv_vectorize(invalid_image_bytes)
            except Exception:
                pass  # Expected to fail

        end_time = time.time()
        total_time = end_time - start_time

        # Error handling should be fast
        assert total_time < 1.0, f"Error handling took too long: {total_time}s"

    @pytest.mark.performance
    @pytest.mark.asyncio
    async def test_edge_case_performance(self, vectorizer_service):
        """Test performance with edge cases."""
        from PIL import Image
        import io

        # Completely black image
        black_img = Image.new('RGB', (100, 100), 'black')
        black_buffer = io.BytesIO()
        black_img.save(black_buffer, format='PNG')
        black_bytes = black_buffer.getvalue()

        # Completely white image
        white_img = Image.new('RGB', (100, 100), 'white')
        white_buffer = io.BytesIO()
        white_img.save(white_buffer, format='PNG')
        white_bytes = white_buffer.getvalue()

        # Test both edge cases
        for image_bytes, name in [(black_bytes, "black"), (white_bytes, "white")]:
            start_time = time.time()
            result = await vectorizer_service.opencv_vectorize(image_bytes)
            end_time = time.time()

            assert '<svg' in result
            duration = end_time - start_time
            assert duration < 2.0, f"{name} image took too long: {duration}s"
            print(f"{name.capitalize()} image: {duration:.3f}s")


class TestAPIPerformance:
    """Performance tests for API endpoints."""

    @pytest.mark.performance
    @pytest.mark.integration
    def test_vectorize_endpoint_performance(self, client, performance_test_image, benchmark):
        """Benchmark the /vectorize endpoint performance."""
        import io

        def make_request():
            files = {"file": ("test.png", io.BytesIO(performance_test_image), "image/png")}
            data = {
                "parameters": "{}",
                "selected_method": "opencv"
            }
            response = client.post("/vectorize", files=files, data=data)
            assert response.status_code == 200
            return response.json()

        result = benchmark.pedantic(make_request, rounds=3, iterations=1)
        assert result["success"] is True

    @pytest.mark.performance
    @pytest.mark.integration
    def test_concurrent_api_requests(self, client, sample_image_bytes):
        """Test API performance under concurrent load."""
        import threading
        import io
        import time

        results = []
        errors = []

        def make_request():
            try:
                files = {"file": ("test.png", io.BytesIO(sample_image_bytes), "image/png")}
                data = {
                    "parameters": "{}",
                    "selected_method": "opencv_edge"
                }
                response = client.post("/vectorize", files=files, data=data)
                results.append(response.status_code)
            except Exception as e:
                errors.append(str(e))

        # Create 10 concurrent requests
        threads = []
        start_time = time.time()

        for _ in range(10):
            thread = threading.Thread(target=make_request)
            threads.append(thread)
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        end_time = time.time()
        total_time = end_time - start_time

        # All requests should succeed
        assert len(errors) == 0, f"Errors occurred: {errors}"
        assert all(status == 200 for status in results), f"Non-200 responses: {results}"

        # Should handle 10 concurrent requests reasonably quickly
        assert total_time < 15.0, f"Concurrent requests took too long: {total_time}s"
        print(f"\n10 concurrent API requests: {total_time:.3f}s")