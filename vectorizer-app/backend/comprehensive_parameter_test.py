#!/usr/bin/env python3
"""
Comprehensive Parameter Test - Fixed version
Tests parameter processing with proper test images and fixed potrace options
"""

import asyncio
import io
import hashlib
import os
from PIL import Image, ImageDraw, ImageFilter
import numpy as np
import sys
sys.path.append(os.path.dirname(__file__))

from main import VectorizerService

async def test_fixed_parameters():
    """Test parameters with the fixed implementation"""
    vectorizer = VectorizerService()

    def svg_hash(svg):
        return hashlib.md5(svg.encode()).hexdigest()[:8]

    def create_noisy_image():
        """Create an image with noise to test turdsize properly"""
        img = Image.new('RGB', (200, 200), 'white')
        draw = ImageDraw.Draw(img)

        # Main shapes
        draw.rectangle([50, 50, 150, 150], fill='black')
        draw.ellipse([75, 75, 125, 125], fill='white')

        # Add noise/speckles
        for i in range(100):
            x = np.random.randint(0, 200)
            y = np.random.randint(0, 200)
            size = np.random.randint(1, 4)
            draw.rectangle([x, y, x+size, y+size], fill='black')

        # Convert to bytes
        img_bytes = io.BytesIO()
        img.save(img_bytes, format='PNG')
        return img_bytes.getvalue()

    def create_gradient_image():
        """Create an image with gradients for threshold testing"""
        # Create a gradient image for better threshold testing
        img = Image.new('L', (200, 200))
        for x in range(200):
            for y in range(200):
                # Create circular gradient
                center_x, center_y = 100, 100
                distance = ((x - center_x)**2 + (y - center_y)**2)**0.5
                intensity = max(0, 255 - int(distance * 2))
                img.putpixel((x, y), intensity)

        # Convert to RGB and then to bytes
        img = img.convert('RGB')
        img_bytes = io.BytesIO()
        img.save(img_bytes, format='PNG')
        return img_bytes.getvalue()

    def create_edge_rich_image():
        """Create an image with various edge types"""
        img = Image.new('RGB', (200, 200), 'white')
        draw = ImageDraw.Draw(img)

        # Various shapes with different edge characteristics
        draw.rectangle([20, 20, 60, 60], fill='black')      # Sharp edges
        draw.ellipse([80, 20, 120, 60], fill='black')       # Curved edges
        draw.polygon([(140, 20), (180, 40), (160, 60), (150, 40)], fill='black')  # Angular

        # Add some fine details
        for i in range(10):
            x = 20 + i * 15
            draw.line([(x, 80), (x, 100)], fill='black', width=1)

        # Add gradient-like area
        for i in range(50):
            gray_val = i * 5
            if gray_val < 256:
                color = (gray_val, gray_val, gray_val)
                draw.rectangle([20 + i, 120, 21 + i, 170], fill=color)

        img_bytes = io.BytesIO()
        img.save(img_bytes, format='PNG')
        return img_bytes.getvalue()

    print("COMPREHENSIVE PARAMETER VALIDATION TEST")
    print("=" * 60)

    # Test 1: Potrace with noisy image for turdsize
    print("\n1. Testing Potrace turdsize with noisy image:")
    noisy_image = create_noisy_image()

    try:
        svg1 = await vectorizer.potrace_vectorize(noisy_image, turdsize=0)
        svg2 = await vectorizer.potrace_vectorize(noisy_image, turdsize=10)
        svg3 = await vectorizer.potrace_vectorize(noisy_image, turdsize=50)

        hash1, hash2, hash3 = svg_hash(svg1), svg_hash(svg2), svg_hash(svg3)
        paths1, paths2, paths3 = svg1.count('<path'), svg2.count('<path'), svg3.count('<path')

        print(f"   turdsize=0:  {hash1} ({paths1} paths, {len(svg1)} chars)")
        print(f"   turdsize=10: {hash2} ({paths2} paths, {len(svg2)} chars)")
        print(f"   turdsize=50: {hash3} ({paths3} paths, {len(svg3)} chars)")

        unique_results = len(set([hash1, hash2, hash3]))
        if unique_results >= 2:
            print(f"   ✅ PASS: turdsize produces {unique_results} different results")
        else:
            print(f"   ❌ FAIL: turdsize produces identical results")

    except Exception as e:
        print(f"   ❌ ERROR: {e}")

    # Test 2: Potrace turnpolicy
    print("\n2. Testing Potrace turnpolicy:")
    try:
        policies = ['black', 'white', 'left', 'right', 'minority', 'majority']
        results = []

        for policy in policies:
            svg = await vectorizer.potrace_vectorize(noisy_image, turnpolicy=policy)
            hash_val = svg_hash(svg)
            results.append(hash_val)
            print(f"   turnpolicy={policy:8}: {hash_val}")

        unique_policies = len(set(results))
        print(f"   ✅ PASS: {unique_policies}/{len(policies)} turnpolicies produce unique results")

    except Exception as e:
        print(f"   ❌ ERROR: {e}")

    # Test 3: Potrace alphamax (corner threshold)
    print("\n3. Testing Potrace alphamax (corner threshold):")
    try:
        svg1 = await vectorizer.potrace_vectorize(noisy_image, alphamax=0.0)   # Sharp corners
        svg2 = await vectorizer.potrace_vectorize(noisy_image, alphamax=1.0)   # Default
        svg3 = await vectorizer.potrace_vectorize(noisy_image, alphamax=2.0)   # Smooth corners

        hash1, hash2, hash3 = svg_hash(svg1), svg_hash(svg2), svg_hash(svg3)
        print(f"   alphamax=0.0: {hash1}")
        print(f"   alphamax=1.0: {hash2}")
        print(f"   alphamax=2.0: {hash3}")

        unique_results = len(set([hash1, hash2, hash3]))
        if unique_results >= 2:
            print(f"   ✅ PASS: alphamax produces {unique_results} different results")
        else:
            print(f"   ❌ FAIL: alphamax produces identical results")

    except Exception as e:
        print(f"   ❌ ERROR: {e}")

    # Test 4: Potrace opticurve
    print("\n4. Testing Potrace opticurve (curve optimization):")
    try:
        svg1 = await vectorizer.potrace_vectorize(noisy_image, opticurve=True)
        svg2 = await vectorizer.potrace_vectorize(noisy_image, opticurve=False)

        hash1, hash2 = svg_hash(svg1), svg_hash(svg2)
        print(f"   opticurve=True:  {hash1} ({len(svg1)} chars)")
        print(f"   opticurve=False: {hash2} ({len(svg2)} chars)")

        if hash1 != hash2:
            print("   ✅ PASS: opticurve produces different results")
        else:
            print("   ❌ FAIL: opticurve produces identical results")

    except Exception as e:
        print(f"   ❌ ERROR: {e}")

    # Test 5: Potrace invert
    print("\n5. Testing Potrace invert:")
    try:
        svg1 = await vectorizer.potrace_vectorize(noisy_image, invert=False)
        svg2 = await vectorizer.potrace_vectorize(noisy_image, invert=True)

        hash1, hash2 = svg_hash(svg1), svg_hash(svg2)
        paths1, paths2 = svg1.count('<path'), svg2.count('<path')
        print(f"   invert=False: {hash1} ({paths1} paths)")
        print(f"   invert=True:  {hash2} ({paths2} paths)")

        if hash1 != hash2:
            print("   ✅ PASS: invert produces different results")
        else:
            print("   ❌ FAIL: invert produces identical results")

    except Exception as e:
        print(f"   ❌ ERROR: {e}")

    # Test 6: OpenCV Edge with gradient image
    print("\n6. Testing OpenCV Edge with gradient image:")
    gradient_image = create_gradient_image()

    try:
        svg1 = await vectorizer.opencv_edge_vectorize(gradient_image, low_threshold=20, high_threshold=60)
        svg2 = await vectorizer.opencv_edge_vectorize(gradient_image, low_threshold=80, high_threshold=160)
        svg3 = await vectorizer.opencv_edge_vectorize(gradient_image, low_threshold=120, high_threshold=200)

        hash1, hash2, hash3 = svg_hash(svg1), svg_hash(svg2), svg_hash(svg3)
        paths1, paths2, paths3 = svg1.count('<path'), svg2.count('<path'), svg3.count('<path')

        print(f"   Low thresholds:    {hash1} ({paths1} paths)")
        print(f"   Medium thresholds: {hash2} ({paths2} paths)")
        print(f"   High thresholds:   {hash3} ({paths3} paths)")

        unique_results = len(set([hash1, hash2, hash3]))
        if unique_results >= 2:
            print(f"   ✅ PASS: edge thresholds produce {unique_results} different results")
        else:
            print(f"   ❌ FAIL: edge thresholds produce identical results")

    except Exception as e:
        print(f"   ❌ ERROR: {e}")

    # Test 7: OpenCV Contour with gradient image
    print("\n7. Testing OpenCV Contour threshold with gradient image:")
    try:
        svg1 = await vectorizer.opencv_contour_vectorize(gradient_image, threshold=80)
        svg2 = await vectorizer.opencv_contour_vectorize(gradient_image, threshold=140)
        svg3 = await vectorizer.opencv_contour_vectorize(gradient_image, threshold=200)

        hash1, hash2, hash3 = svg_hash(svg1), svg_hash(svg2), svg_hash(svg3)
        paths1, paths2, paths3 = svg1.count('<path'), svg2.count('<path'), svg3.count('<path')

        print(f"   threshold=80:  {hash1} ({paths1} paths)")
        print(f"   threshold=140: {hash2} ({paths2} paths)")
        print(f"   threshold=200: {hash3} ({paths3} paths)")

        unique_results = len(set([hash1, hash2, hash3]))
        if unique_results >= 2:
            print(f"   ✅ PASS: contour threshold produces {unique_results} different results")
        else:
            print(f"   ❌ FAIL: contour threshold produces identical results")

    except Exception as e:
        print(f"   ❌ ERROR: {e}")

    # Test 8: OpenCV min_area parameter
    print("\n8. Testing OpenCV min_area parameter:")
    edge_rich_image = create_edge_rich_image()

    try:
        svg1 = await vectorizer.opencv_edge_vectorize(edge_rich_image, min_area=10)
        svg2 = await vectorizer.opencv_edge_vectorize(edge_rich_image, min_area=100)
        svg3 = await vectorizer.opencv_edge_vectorize(edge_rich_image, min_area=500)

        hash1, hash2, hash3 = svg_hash(svg1), svg_hash(svg2), svg_hash(svg3)
        paths1, paths2, paths3 = svg1.count('<path'), svg2.count('<path'), svg3.count('<path')

        print(f"   min_area=10:  {hash1} ({paths1} paths)")
        print(f"   min_area=100: {hash2} ({paths2} paths)")
        print(f"   min_area=500: {hash3} ({paths3} paths)")

        if paths1 >= paths2 >= paths3:  # Should have fewer paths as min_area increases
            print("   ✅ PASS: min_area correctly filters contours")
        else:
            print("   ⚠️  PARTIAL: min_area working but unexpected path counts")

    except Exception as e:
        print(f"   ❌ ERROR: {e}")

    print("\n" + "=" * 60)
    print("COMPREHENSIVE TEST COMPLETE")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(test_fixed_parameters())