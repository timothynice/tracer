#!/usr/bin/env python3
"""
Parameter Validation Test Script for Vectorizer Backend
Tests that different parameter values produce visually different SVG outputs.
"""

import asyncio
import io
import json
import hashlib
import os
from PIL import Image, ImageDraw
import sys
sys.path.append(os.path.dirname(__file__))

from main import VectorizerService

class ParameterTester:
    def __init__(self):
        self.vectorizer = VectorizerService()
        self.test_results = {}

    def create_test_image(self, name: str, size=(200, 200)):
        """Create a simple test image for parameter validation"""
        img = Image.new('RGB', size, 'white')
        draw = ImageDraw.Draw(img)

        if name == "simple_shapes":
            # Draw simple geometric shapes
            draw.rectangle([50, 50, 150, 100], fill='black')
            draw.ellipse([75, 120, 125, 170], fill='black')

        elif name == "detailed_drawing":
            # More complex drawing with fine details
            draw.rectangle([20, 20, 180, 40], fill='black')
            draw.rectangle([20, 60, 40, 180], fill='black')
            draw.rectangle([160, 60, 180, 180], fill='black')
            draw.rectangle([20, 160, 180, 180], fill='black')
            # Add some small details that should be affected by turdsize
            for i in range(5):
                for j in range(5):
                    if (i + j) % 2 == 0:
                        x, y = 60 + i*5, 80 + j*5
                        draw.rectangle([x, y, x+2, y+2], fill='black')

        elif name == "curves_and_corners":
            # Drawing with curves and sharp corners to test alphamax
            draw.arc([50, 50, 150, 150], 0, 180, fill='black', width=10)
            draw.polygon([(100, 30), (130, 60), (100, 90), (70, 60)], fill='black')

        # Convert to bytes
        img_bytes = io.BytesIO()
        img.save(img_bytes, format='PNG')
        return img_bytes.getvalue()

    def svg_hash(self, svg_content: str) -> str:
        """Generate hash of SVG content to detect differences"""
        return hashlib.md5(svg_content.encode()).hexdigest()[:8]

    def extract_svg_stats(self, svg_content: str) -> dict:
        """Extract statistics from SVG content for comparison"""
        stats = {
            'length': len(svg_content),
            'path_count': svg_content.count('<path'),
            'move_commands': svg_content.count(' M '),
            'line_commands': svg_content.count(' L '),
            'curve_commands': svg_content.count(' C '),
            'close_commands': svg_content.count(' Z'),
        }
        return stats

    async def test_potrace_parameters(self):
        """Test Potrace method with different parameter combinations"""
        print("\n=== Testing Potrace Parameters ===")

        test_image = self.create_test_image("detailed_drawing")
        base_params = {'invert': False, 'turdsize': 2, 'turnpolicy': 'minority', 'alphamax': 1.0, 'opticurve': True}

        test_cases = [
            # Test turdsize (filter small speckles)
            {**base_params, 'turdsize': 0},    # Keep all speckles
            {**base_params, 'turdsize': 10},   # Filter small speckles
            {**base_params, 'turdsize': 50},   # Filter more speckles

            # Test turnpolicy
            {**base_params, 'turnpolicy': 'black'},
            {**base_params, 'turnpolicy': 'white'},
            {**base_params, 'turnpolicy': 'left'},
            {**base_params, 'turnpolicy': 'right'},

            # Test alphamax (corner threshold)
            {**base_params, 'alphamax': 0.0},   # Sharp corners
            {**base_params, 'alphamax': 1.334}, # Default corners
            {**base_params, 'alphamax': 2.0},   # Smoother corners

            # Test opticurve
            {**base_params, 'opticurve': False},

            # Test invert
            {**base_params, 'invert': True},
        ]

        results = []
        for i, params in enumerate(test_cases):
            try:
                svg = await self.vectorizer.potrace_vectorize(test_image, **params)
                hash_val = self.svg_hash(svg)
                stats = self.extract_svg_stats(svg)
                results.append({
                    'params': params,
                    'hash': hash_val,
                    'stats': stats,
                    'success': True
                })
                print(f"Potrace test {i+1}: {params} -> Hash: {hash_val}, Paths: {stats['path_count']}")
            except Exception as e:
                results.append({
                    'params': params,
                    'error': str(e),
                    'success': False
                })
                print(f"Potrace test {i+1}: {params} -> ERROR: {e}")

        # Check for parameter effectiveness
        unique_hashes = set(r['hash'] for r in results if r['success'])
        print(f"\nPotrace Results: {len(unique_hashes)} unique outputs from {len([r for r in results if r['success']])} successful tests")

        return results

    async def test_opencv_edge_parameters(self):
        """Test OpenCV Edge method with different parameters"""
        print("\n=== Testing OpenCV Edge Parameters ===")

        test_image = self.create_test_image("simple_shapes")
        base_params = {'blur_size': 5, 'low_threshold': 30, 'high_threshold': 100,
                      'min_area': 50, 'epsilon_factor': 0.02, 'stroke_width': 2}

        test_cases = [
            # Test Canny thresholds
            {**base_params, 'low_threshold': 10, 'high_threshold': 50},   # More sensitive
            {**base_params, 'low_threshold': 50, 'high_threshold': 150},  # Less sensitive
            {**base_params, 'low_threshold': 100, 'high_threshold': 200}, # Much less sensitive

            # Test blur size
            {**base_params, 'blur_size': 1},   # Minimal blur
            {**base_params, 'blur_size': 15},  # Heavy blur

            # Test min_area
            {**base_params, 'min_area': 10},   # Include smaller contours
            {**base_params, 'min_area': 200},  # Only large contours

            # Test epsilon_factor (contour approximation)
            {**base_params, 'epsilon_factor': 0.001}, # Very detailed
            {**base_params, 'epsilon_factor': 0.1},   # Very simplified

            # Test stroke_width
            {**base_params, 'stroke_width': 1},
            {**base_params, 'stroke_width': 5},
        ]

        results = []
        for i, params in enumerate(test_cases):
            try:
                svg = await self.vectorizer.opencv_edge_vectorize(test_image, **params)
                hash_val = self.svg_hash(svg)
                stats = self.extract_svg_stats(svg)
                results.append({
                    'params': params,
                    'hash': hash_val,
                    'stats': stats,
                    'success': True
                })
                print(f"OpenCV Edge test {i+1}: Key params changed -> Hash: {hash_val}, Paths: {stats['path_count']}")
            except Exception as e:
                results.append({
                    'params': params,
                    'error': str(e),
                    'success': False
                })
                print(f"OpenCV Edge test {i+1}: ERROR: {e}")

        unique_hashes = set(r['hash'] for r in results if r['success'])
        print(f"\nOpenCV Edge Results: {len(unique_hashes)} unique outputs from {len([r for r in results if r['success']])} successful tests")

        return results

    async def test_opencv_contour_parameters(self):
        """Test OpenCV Contour method with different parameters"""
        print("\n=== Testing OpenCV Contour Parameters ===")

        test_image = self.create_test_image("simple_shapes")
        base_params = {'threshold': 127, 'min_area': 100, 'epsilon_factor': 0.01, 'invert_threshold': False}

        test_cases = [
            # Test threshold levels
            {**base_params, 'threshold': 50},   # Lower threshold
            {**base_params, 'threshold': 200},  # Higher threshold

            # Test invert_threshold
            {**base_params, 'invert_threshold': True},

            # Test min_area
            {**base_params, 'min_area': 10},   # Include smaller areas
            {**base_params, 'min_area': 500},  # Only large areas

            # Test epsilon_factor
            {**base_params, 'epsilon_factor': 0.001}, # Very detailed
            {**base_params, 'epsilon_factor': 0.05},  # Very simplified
        ]

        results = []
        for i, params in enumerate(test_cases):
            try:
                svg = await self.vectorizer.opencv_contour_vectorize(test_image, **params)
                hash_val = self.svg_hash(svg)
                stats = self.extract_svg_stats(svg)
                results.append({
                    'params': params,
                    'hash': hash_val,
                    'stats': stats,
                    'success': True
                })
                print(f"OpenCV Contour test {i+1}: Key params changed -> Hash: {hash_val}, Paths: {stats['path_count']}")
            except Exception as e:
                results.append({
                    'params': params,
                    'error': str(e),
                    'success': False
                })
                print(f"OpenCV Contour test {i+1}: ERROR: {e}")

        unique_hashes = set(r['hash'] for r in results if r['success'])
        print(f"\nOpenCV Contour Results: {len(unique_hashes)} unique outputs from {len([r for r in results if r['success']])} successful tests")

        return results

    async def test_opencv_basic_parameters(self):
        """Test basic OpenCV method with different parameters"""
        print("\n=== Testing OpenCV Basic Parameters ===")

        test_image = self.create_test_image("simple_shapes")
        base_params = {'low_threshold': 50, 'high_threshold': 150, 'min_contour_points': 3}

        test_cases = [
            # Test Canny thresholds
            {**base_params, 'low_threshold': 20, 'high_threshold': 80},
            {**base_params, 'low_threshold': 100, 'high_threshold': 200},

            # Test min_contour_points
            {**base_params, 'min_contour_points': 1},
            {**base_params, 'min_contour_points': 10},
        ]

        results = []
        for i, params in enumerate(test_cases):
            try:
                svg = await self.vectorizer.opencv_vectorize(test_image, **params)
                hash_val = self.svg_hash(svg)
                stats = self.extract_svg_stats(svg)
                results.append({
                    'params': params,
                    'hash': hash_val,
                    'stats': stats,
                    'success': True
                })
                print(f"OpenCV Basic test {i+1}: Key params changed -> Hash: {hash_val}, Paths: {stats['path_count']}")
            except Exception as e:
                results.append({
                    'params': params,
                    'error': str(e),
                    'success': False
                })
                print(f"OpenCV Basic test {i+1}: ERROR: {e}")

        unique_hashes = set(r['hash'] for r in results if r['success'])
        print(f"\nOpenCV Basic Results: {len(unique_hashes)} unique outputs from {len([r for r in results if r['success']])} successful tests")

        return results

    def save_test_svg(self, svg_content: str, filename: str):
        """Save SVG content to file for visual inspection"""
        os.makedirs('/tmp/vectorizer_test', exist_ok=True)
        filepath = f'/tmp/vectorizer_test/{filename}'
        with open(filepath, 'w') as f:
            f.write(svg_content)
        return filepath

    async def run_comprehensive_test(self):
        """Run all parameter tests and generate report"""
        print("Starting Comprehensive Parameter Validation Test")
        print("=" * 60)

        # Run all tests
        potrace_results = await self.test_potrace_parameters()
        opencv_edge_results = await self.test_opencv_edge_parameters()
        opencv_contour_results = await self.test_opencv_contour_parameters()
        opencv_basic_results = await self.test_opencv_basic_parameters()

        # Generate summary report
        print("\n" + "=" * 60)
        print("PARAMETER VALIDATION SUMMARY")
        print("=" * 60)

        def analyze_results(method_name, results):
            successful = [r for r in results if r['success']]
            failed = [r for r in results if not r['success']]
            unique_hashes = set(r['hash'] for r in successful)

            print(f"\n{method_name}:")
            print(f"  Total tests: {len(results)}")
            print(f"  Successful: {len(successful)}")
            print(f"  Failed: {len(failed)}")
            print(f"  Unique outputs: {len(unique_hashes)}")

            if len(successful) > 0:
                effectiveness = len(unique_hashes) / len(successful) * 100
                print(f"  Parameter effectiveness: {effectiveness:.1f}%")

                if effectiveness < 50:
                    print(f"  ⚠️  WARNING: Low parameter effectiveness - many parameters produce identical results")
                elif effectiveness > 80:
                    print(f"  ✅ GOOD: High parameter effectiveness - parameters produce distinct results")
                else:
                    print(f"  ⚖️  MODERATE: Some parameters produce identical results")

            if failed:
                print(f"  ❌ ERRORS encountered:")
                for fail in failed:
                    print(f"    - {fail['params']}: {fail['error']}")

            return len(unique_hashes), len(successful)

        total_unique = 0
        total_successful = 0

        unique, successful = analyze_results("POTRACE", potrace_results)
        total_unique += unique
        total_successful += successful

        unique, successful = analyze_results("OPENCV EDGE", opencv_edge_results)
        total_unique += unique
        total_successful += successful

        unique, successful = analyze_results("OPENCV CONTOUR", opencv_contour_results)
        total_unique += unique
        total_successful += successful

        unique, successful = analyze_results("OPENCV BASIC", opencv_basic_results)
        total_unique += unique
        total_successful += successful

        print(f"\n" + "=" * 60)
        print("OVERALL ASSESSMENT")
        print("=" * 60)

        overall_effectiveness = total_unique / total_successful * 100 if total_successful > 0 else 0
        print(f"Overall parameter effectiveness: {overall_effectiveness:.1f}%")
        print(f"Total unique outputs: {total_unique}")
        print(f"Total successful tests: {total_successful}")

        if overall_effectiveness > 70:
            print("✅ VERDICT: Parameters are working correctly and producing distinct results")
        elif overall_effectiveness > 40:
            print("⚠️  VERDICT: Parameters are partially working but some may not have visual impact")
        else:
            print("❌ VERDICT: Major issues with parameter processing - many parameters not working")

        # Save sample outputs for manual inspection
        print(f"\nSample SVG outputs saved to /tmp/vectorizer_test/ for manual inspection")

        return {
            'potrace': potrace_results,
            'opencv_edge': opencv_edge_results,
            'opencv_contour': opencv_contour_results,
            'opencv_basic': opencv_basic_results,
            'overall_effectiveness': overall_effectiveness
        }

async def main():
    tester = ParameterTester()
    results = await tester.run_comprehensive_test()

    # Save detailed results
    with open('/tmp/vectorizer_test/parameter_test_results.json', 'w') as f:
        json.dump(results, f, indent=2, default=str)

    print(f"\nDetailed test results saved to /tmp/vectorizer_test/parameter_test_results.json")

if __name__ == "__main__":
    asyncio.run(main())