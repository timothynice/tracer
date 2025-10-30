#!/usr/bin/env python3
"""
Debug Parameter Processing - Detailed analysis of parameter issues
"""

import asyncio
import io
import subprocess
import tempfile
import os
from PIL import Image, ImageDraw
import sys
sys.path.append(os.path.dirname(__file__))

from main import VectorizerService

async def debug_potrace_parameters():
    """Debug Potrace parameter processing"""
    print("=" * 50)
    print("DEBUGGING POTRACE PARAMETERS")
    print("=" * 50)

    vectorizer = VectorizerService()

    # Create test image with more details
    img = Image.new('RGB', (200, 200), 'white')
    draw = ImageDraw.Draw(img)

    # Main shape
    draw.rectangle([50, 50, 150, 150], fill='black')
    draw.ellipse([75, 75, 125, 125], fill='white')

    # Add small speckles that should be affected by turdsize
    for i in range(10):
        for j in range(10):
            if (i + j) % 7 == 0:  # Create small speckles
                x, y = 20 + i*5, 20 + j*5
                draw.rectangle([x, y, x+1, y+1], fill='black')

    img_bytes = io.BytesIO()
    img.save(img_bytes, format='PNG')
    test_image = img_bytes.getvalue()

    # Test with direct potrace calls to verify parameters work
    print("\nTesting direct potrace command execution:")

    # Save test image to temp file
    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as temp_input:
        temp_input.write(test_image)
        temp_input_path = temp_input.name

    temp_bmp_path = temp_input_path.replace('.png', '.bmp')
    temp_svg1_path = temp_input_path.replace('.png', '_test1.svg')
    temp_svg2_path = temp_input_path.replace('.png', '_test2.svg')

    # Convert to BMP for potrace
    img = Image.open(temp_input_path).convert('L')
    img.save(temp_bmp_path)

    try:
        # Test 1: turdsize=0
        cmd1 = ['potrace', '-s', '--svg', '--output', temp_svg1_path, '--turdsize', '0', temp_bmp_path]
        result1 = subprocess.run(cmd1, capture_output=True, text=True)

        # Test 2: turdsize=10
        cmd2 = ['potrace', '-s', '--svg', '--output', temp_svg2_path, '--turdsize', '10', temp_bmp_path]
        result2 = subprocess.run(cmd2, capture_output=True, text=True)

        if result1.returncode == 0 and result2.returncode == 0:
            with open(temp_svg1_path, 'r') as f:
                svg1_content = f.read()
            with open(temp_svg2_path, 'r') as f:
                svg2_content = f.read()

            print(f"Direct potrace turdsize=0:  length={len(svg1_content)}, paths={svg1_content.count('<path')}")
            print(f"Direct potrace turdsize=10: length={len(svg2_content)}, paths={svg2_content.count('<path')}")

            if svg1_content != svg2_content:
                print("✅ Direct potrace calls with different turdsize produce different results")
            else:
                print("❌ Direct potrace calls with different turdsize produce identical results")
        else:
            print(f"❌ Direct potrace failed: {result1.stderr} | {result2.stderr}")

    except Exception as e:
        print(f"❌ Direct potrace test failed: {e}")

    # Test our wrapper method with debug prints
    print("\nTesting our potrace_vectorize method:")

    # Monkey patch the method to add debug prints
    original_method = vectorizer.potrace_vectorize

    async def debug_potrace_vectorize(image_bytes, **kwargs):
        print(f"DEBUG: potrace_vectorize called with kwargs: {kwargs}")

        # Create temporary files
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as temp_input:
            temp_input.write(image_bytes)
            temp_input_path = temp_input.name

        temp_svg_path = temp_input_path.replace('.png', '.svg')
        temp_bmp_path = temp_input_path.replace('.png', '.bmp')

        # Convert to BMP and optionally invert
        img = Image.open(temp_input_path).convert('L')

        if kwargs.get('invert', False):
            print("DEBUG: Applying image inversion")
            img = Image.eval(img, lambda x: 255 - x)

        img.save(temp_bmp_path)

        # Build potrace command with parameters
        cmd = ['potrace', '-s', '--svg', '--output', temp_svg_path]

        # Add turdsize (filter small speckles)
        turdsize = kwargs.get('turdsize', 2)
        cmd.extend(['--turdsize', str(turdsize)])
        print(f"DEBUG: Added turdsize: {turdsize}")

        # Add turn policy
        turnpolicy = kwargs.get('turnpolicy', 'minority')
        cmd.extend(['--turnpolicy', turnpolicy])
        print(f"DEBUG: Added turnpolicy: {turnpolicy}")

        # Add corner threshold
        alphamax = kwargs.get('alphamax', 1.0)
        cmd.extend(['--alphamax', str(alphamax)])
        print(f"DEBUG: Added alphamax: {alphamax}")

        # Add curve optimization
        opticurve = kwargs.get('opticurve', True)
        if opticurve:
            cmd.append('--opticurve')
        else:
            cmd.append('--noopticurve')
        print(f"DEBUG: Added opticurve: {opticurve}")

        cmd.append(temp_bmp_path)
        print(f"DEBUG: Final command: {' '.join(cmd)}")

        # Run potrace
        result = subprocess.run(cmd, capture_output=True, text=True)
        print(f"DEBUG: Potrace return code: {result.returncode}")
        if result.stderr:
            print(f"DEBUG: Potrace stderr: {result.stderr}")

        if result.returncode != 0:
            raise Exception(f"Potrace failed: {result.stderr}")

        # Read SVG content
        with open(temp_svg_path, 'r') as f:
            svg_content = f.read()

        print(f"DEBUG: SVG length: {len(svg_content)}, paths: {svg_content.count('<path')}")

        # Cleanup
        for path in [temp_input_path, temp_svg_path, temp_bmp_path]:
            if os.path.exists(path):
                os.unlink(path)

        return svg_content

    vectorizer.potrace_vectorize = debug_potrace_vectorize

    print("\nTesting turdsize parameter with debug output:")
    try:
        svg1 = await vectorizer.potrace_vectorize(test_image, turdsize=0)
        print("\n---")
        svg2 = await vectorizer.potrace_vectorize(test_image, turdsize=10)

        print(f"\nResult comparison:")
        print(f"turdsize=0:  {len(svg1)} chars, {svg1.count('<path')} paths")
        print(f"turdsize=10: {len(svg2)} chars, {svg2.count('<path')} paths")

        if svg1 != svg2:
            print("✅ Our method: turdsize produces different results")
        else:
            print("❌ Our method: turdsize produces identical results")

    except Exception as e:
        print(f"❌ Our method failed: {e}")

    # Cleanup temp files
    for path in [temp_input_path, temp_bmp_path, temp_svg1_path, temp_svg2_path]:
        if os.path.exists(path):
            os.unlink(path)

async def debug_opencv_parameters():
    """Debug OpenCV parameter processing"""
    print("\n" + "=" * 50)
    print("DEBUGGING OPENCV PARAMETERS")
    print("=" * 50)

    vectorizer = VectorizerService()

    # Create a test image with clear edges
    img = Image.new('RGB', (100, 100), 'white')
    draw = ImageDraw.Draw(img)
    draw.rectangle([20, 20, 80, 80], fill='black')

    img_bytes = io.BytesIO()
    img.save(img_bytes, format='PNG')
    test_image = img_bytes.getvalue()

    # Monkey patch opencv_edge_vectorize for debugging
    original_method = vectorizer.opencv_edge_vectorize

    async def debug_opencv_edge_vectorize(image_bytes, **kwargs):
        import cv2
        import numpy as np

        print(f"DEBUG: opencv_edge_vectorize called with kwargs: {kwargs}")

        # Convert bytes to numpy array
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        # Apply Gaussian blur to reduce noise
        blur_size = kwargs.get('blur_size', 5)
        blurred = cv2.GaussianBlur(gray, (blur_size, blur_size), 0)
        print(f"DEBUG: Applied blur_size: {blur_size}")

        # Enhanced edge detection
        low_threshold = kwargs.get('low_threshold', 30)
        high_threshold = kwargs.get('high_threshold', 100)
        edges = cv2.Canny(blurred, low_threshold, high_threshold, apertureSize=3)
        print(f"DEBUG: Canny thresholds: low={low_threshold}, high={high_threshold}")

        # Count edge pixels for debugging
        edge_pixels = np.count_nonzero(edges)
        print(f"DEBUG: Edge pixels detected: {edge_pixels}")

        # Find contours
        contours, _ = cv2.findContours(edges, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        print(f"DEBUG: Found {len(contours)} contours")

        height, width = gray.shape
        svg_content = f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
'''

        # Process contours with minimum area filter
        min_area = kwargs.get('min_area', 50)
        epsilon_factor = kwargs.get('epsilon_factor', 0.02)
        stroke_width = kwargs.get('stroke_width', 2)

        valid_contours = 0
        for contour in contours:
            area = cv2.contourArea(contour)
            if area > min_area:  # Filter small noise
                valid_contours += 1
                # Approximate contour to reduce points
                epsilon = epsilon_factor * cv2.arcLength(contour, True)
                approx = cv2.approxPolyDP(contour, epsilon, True)

                if len(approx) > 2:
                    path_data = f"M {approx[0][0][0]},{approx[0][0][1]}"
                    for point in approx[1:]:
                        path_data += f" L {point[0][0]},{point[0][1]}"
                    path_data += " Z"

                    svg_content += f'<path d="{path_data}" fill="none" stroke="black" stroke-width="{stroke_width}"/>\n'

        svg_content += '</svg>'

        print(f"DEBUG: Valid contours after filtering (min_area={min_area}): {valid_contours}")
        print(f"DEBUG: Final SVG length: {len(svg_content)}")

        return svg_content

    vectorizer.opencv_edge_vectorize = debug_opencv_edge_vectorize

    print("\nTesting OpenCV edge detection thresholds with debug output:")
    try:
        svg1 = await vectorizer.opencv_edge_vectorize(test_image, low_threshold=10, high_threshold=50)
        print("\n---")
        svg2 = await vectorizer.opencv_edge_vectorize(test_image, low_threshold=100, high_threshold=200)

        print(f"\nResult comparison:")
        print(f"Low thresholds:  {len(svg1)} chars, {svg1.count('<path')} paths")
        print(f"High thresholds: {len(svg2)} chars, {svg2.count('<path')} paths")

        if svg1 != svg2:
            print("✅ OpenCV edge: thresholds produce different results")
        else:
            print("❌ OpenCV edge: thresholds produce identical results")

    except Exception as e:
        print(f"❌ OpenCV edge method failed: {e}")

async def main():
    """Run comprehensive parameter debugging"""
    print("PARAMETER PROCESSING DEBUG ANALYSIS")
    print("This will help identify why parameters aren't producing different outputs")

    await debug_potrace_parameters()
    await debug_opencv_parameters()

    print("\n" + "=" * 70)
    print("DEBUGGING COMPLETE")
    print("=" * 70)

if __name__ == "__main__":
    asyncio.run(main())