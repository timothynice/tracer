#!/usr/bin/env python3
"""
Visual Parameter Validation - Creates sample SVG outputs for manual inspection
Generates SVG files showing the visual impact of different parameters
"""

import asyncio
import io
import os
from PIL import Image, ImageDraw
import sys
sys.path.append(os.path.dirname(__file__))

from main import VectorizerService

async def create_visual_validation_samples():
    """Create sample SVG outputs for visual comparison"""
    vectorizer = VectorizerService()

    # Create output directory
    output_dir = "/tmp/vectorizer_validation"
    os.makedirs(output_dir, exist_ok=True)

    def create_test_image():
        """Create a comprehensive test image"""
        img = Image.new('RGB', (300, 300), 'white')
        draw = ImageDraw.Draw(img)

        # Main geometric shapes
        draw.rectangle([50, 50, 150, 150], fill='black')
        draw.ellipse([170, 50, 250, 130], fill='black')
        draw.polygon([(50, 200), (100, 170), (150, 200), (125, 250), (75, 250)], fill='black')

        # Fine details that should be affected by parameters
        for i in range(20):
            for j in range(20):
                if (i + j) % 5 == 0:
                    x, y = 20 + i*3, 20 + j*3
                    draw.rectangle([x, y, x+1, y+1], fill='black')

        # Text-like patterns
        for i in range(5):
            y_pos = 280
            x_start = 50 + i * 30
            draw.rectangle([x_start, y_pos, x_start + 20, y_pos + 15], fill='black')
            draw.rectangle([x_start + 5, y_pos - 10, x_start + 15, y_pos], fill='black')

        img_bytes = io.BytesIO()
        img.save(img_bytes, format='PNG')
        return img_bytes.getvalue()

    def save_svg(svg_content, filename, description=""):
        """Save SVG with description"""
        filepath = os.path.join(output_dir, filename)
        with open(filepath, 'w') as f:
            f.write(svg_content)

        # Also save a description
        desc_path = filepath.replace('.svg', '_description.txt')
        with open(desc_path, 'w') as f:
            f.write(f"File: {filename}\n")
            f.write(f"Description: {description}\n")
            f.write(f"SVG Length: {len(svg_content)} characters\n")
            f.write(f"Path Count: {svg_content.count('<path')} paths\n")
            f.write(f"Move Commands: {svg_content.count(' M ')}\n")
            f.write(f"Line Commands: {svg_content.count(' L ')}\n")

        print(f"Saved: {filename} - {description}")
        return filepath

    print("Creating Visual Parameter Validation Samples")
    print("=" * 60)

    test_image = create_test_image()

    # Save the original test image for reference
    with open(os.path.join(output_dir, 'original_test_image.png'), 'wb') as f:
        f.write(test_image)

    print(f"Output directory: {output_dir}")
    print(f"Original test image saved: original_test_image.png")
    print()

    # Potrace parameter variations
    print("Generating Potrace parameter samples...")

    # Turdsize variations
    try:
        svg = await vectorizer.potrace_vectorize(test_image, turdsize=0)
        save_svg(svg, "potrace_turdsize_0.svg", "Potrace with turdsize=0 (keep all speckles)")

        svg = await vectorizer.potrace_vectorize(test_image, turdsize=10)
        save_svg(svg, "potrace_turdsize_10.svg", "Potrace with turdsize=10 (filter small speckles)")

        svg = await vectorizer.potrace_vectorize(test_image, turdsize=50)
        save_svg(svg, "potrace_turdsize_50.svg", "Potrace with turdsize=50 (filter more speckles)")
    except Exception as e:
        print(f"❌ Potrace turdsize test failed: {e}")

    # Alphamax variations
    try:
        svg = await vectorizer.potrace_vectorize(test_image, alphamax=0.0)
        save_svg(svg, "potrace_alphamax_0.svg", "Potrace with alphamax=0.0 (sharp corners)")

        svg = await vectorizer.potrace_vectorize(test_image, alphamax=1.0)
        save_svg(svg, "potrace_alphamax_1.svg", "Potrace with alphamax=1.0 (default corners)")

        svg = await vectorizer.potrace_vectorize(test_image, alphamax=2.0)
        save_svg(svg, "potrace_alphamax_2.svg", "Potrace with alphamax=2.0 (smooth corners)")
    except Exception as e:
        print(f"❌ Potrace alphamax test failed: {e}")

    # Invert test
    try:
        svg = await vectorizer.potrace_vectorize(test_image, invert=False)
        save_svg(svg, "potrace_normal.svg", "Potrace normal (invert=False)")

        svg = await vectorizer.potrace_vectorize(test_image, invert=True)
        save_svg(svg, "potrace_inverted.svg", "Potrace inverted (invert=True)")
    except Exception as e:
        print(f"❌ Potrace invert test failed: {e}")

    # Opticurve test
    try:
        svg = await vectorizer.potrace_vectorize(test_image, opticurve=True)
        save_svg(svg, "potrace_opticurve_on.svg", "Potrace with curve optimization ON")

        svg = await vectorizer.potrace_vectorize(test_image, opticurve=False)
        save_svg(svg, "potrace_opticurve_off.svg", "Potrace with curve optimization OFF")
    except Exception as e:
        print(f"❌ Potrace opticurve test failed: {e}")

    # OpenCV Edge parameter variations
    print("\nGenerating OpenCV Edge parameter samples...")

    try:
        svg = await vectorizer.opencv_edge_vectorize(test_image, low_threshold=20, high_threshold=60)
        save_svg(svg, "opencv_edge_low_thresh.svg", "OpenCV Edge with low thresholds (20,60)")

        svg = await vectorizer.opencv_edge_vectorize(test_image, low_threshold=100, high_threshold=200)
        save_svg(svg, "opencv_edge_high_thresh.svg", "OpenCV Edge with high thresholds (100,200)")

        svg = await vectorizer.opencv_edge_vectorize(test_image, min_area=10)
        save_svg(svg, "opencv_edge_small_areas.svg", "OpenCV Edge with min_area=10 (include small areas)")

        svg = await vectorizer.opencv_edge_vectorize(test_image, min_area=200)
        save_svg(svg, "opencv_edge_large_areas.svg", "OpenCV Edge with min_area=200 (only large areas)")

        svg = await vectorizer.opencv_edge_vectorize(test_image, stroke_width=1)
        save_svg(svg, "opencv_edge_thin_stroke.svg", "OpenCV Edge with stroke_width=1 (thin lines)")

        svg = await vectorizer.opencv_edge_vectorize(test_image, stroke_width=4)
        save_svg(svg, "opencv_edge_thick_stroke.svg", "OpenCV Edge with stroke_width=4 (thick lines)")
    except Exception as e:
        print(f"❌ OpenCV Edge tests failed: {e}")

    # OpenCV Contour parameter variations
    print("\nGenerating OpenCV Contour parameter samples...")

    try:
        svg = await vectorizer.opencv_contour_vectorize(test_image, threshold=80)
        save_svg(svg, "opencv_contour_thresh_80.svg", "OpenCV Contour with threshold=80")

        svg = await vectorizer.opencv_contour_vectorize(test_image, threshold=160)
        save_svg(svg, "opencv_contour_thresh_160.svg", "OpenCV Contour with threshold=160")

        svg = await vectorizer.opencv_contour_vectorize(test_image, invert_threshold=False)
        save_svg(svg, "opencv_contour_normal.svg", "OpenCV Contour normal threshold")

        svg = await vectorizer.opencv_contour_vectorize(test_image, invert_threshold=True)
        save_svg(svg, "opencv_contour_inverted.svg", "OpenCV Contour inverted threshold")

        svg = await vectorizer.opencv_contour_vectorize(test_image, min_area=50)
        save_svg(svg, "opencv_contour_small_areas.svg", "OpenCV Contour with min_area=50")

        svg = await vectorizer.opencv_contour_vectorize(test_image, min_area=500)
        save_svg(svg, "opencv_contour_large_areas.svg", "OpenCV Contour with min_area=500")
    except Exception as e:
        print(f"❌ OpenCV Contour tests failed: {e}")

    print(f"\n" + "=" * 60)
    print("VISUAL VALIDATION SAMPLES COMPLETE")
    print("=" * 60)
    print(f"All samples saved to: {output_dir}")
    print(f"Open the SVG files in a web browser or SVG viewer to compare visual differences.")
    print(f"Each SVG file has a corresponding _description.txt with technical details.")

    # Create an HTML index for easy viewing
    html_content = """
<!DOCTYPE html>
<html>
<head>
    <title>Vectorizer Parameter Validation</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; }
        .sample-group { border: 1px solid #ccc; margin: 20px 0; padding: 15px; }
        .sample { display: inline-block; margin: 10px; text-align: center; }
        .sample svg { border: 1px solid #ddd; max-width: 200px; max-height: 200px; }
        h2 { color: #333; }
        h3 { color: #666; }
    </style>
</head>
<body>
    <h1>Vectorizer Parameter Validation Samples</h1>
    <p>Compare the visual differences between different parameter settings.</p>

    <div class="sample-group">
        <h2>Original Test Image</h2>
        <img src="original_test_image.png" alt="Original" style="max-width: 300px;">
    </div>
"""

    # Add SVG samples to HTML (simplified for now)
    svg_files = [f for f in os.listdir(output_dir) if f.endswith('.svg')]
    for svg_file in sorted(svg_files):
        method = svg_file.split('_')[0]
        html_content += f"""
    <div class="sample">
        <h4>{svg_file.replace('.svg', '').replace('_', ' ').title()}</h4>
        <object data="{svg_file}" type="image/svg+xml" width="200" height="200"></object>
    </div>
"""

    html_content += """
</body>
</html>
"""

    with open(os.path.join(output_dir, 'index.html'), 'w') as f:
        f.write(html_content)

    print(f"Created index.html for easy viewing: file://{output_dir}/index.html")

if __name__ == "__main__":
    asyncio.run(create_visual_validation_samples())