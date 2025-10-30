#!/usr/bin/env python3
import requests
import json
from PIL import Image, ImageDraw
import io

# Create simple test image
img = Image.new('RGB', (50, 50), color='white')
draw = ImageDraw.Draw(img)
draw.rectangle([10, 10, 40, 40], fill='black')

img_bytes = io.BytesIO()
img.save(img_bytes, format='PNG')

# Test with fillcolor
files = {'file': ('test.png', img_bytes.getvalue(), 'image/png')}
params = {
    'potrace': {
        'fillcolor': '#ff0000',  # Red fill
        'color': '#0000ff'       # Blue stroke
    }
}
data = {
    'parameters': json.dumps(params),
    'selected_method': 'potrace'
}

response = requests.post('http://localhost:8000/vectorize', files=files, data=data)
if response.status_code == 200:
    svg = response.json()['vectorized']['potrace']
    print("SVG:")
    print(svg)
else:
    print(f"Error: {response.status_code}")