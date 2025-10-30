<template>
  <div id="app">
    <header>
      <h1>🎨 Image Vectorizer</h1>
      <p>Drag & drop a PNG image to vectorize with multiple methods</p>
    </header>

    <main>
      <!-- Upload Area -->
      <div
        class="upload-area"
        :class="{ 'dragover': isDragOver }"
        @drop="handleDrop"
        @dragover="handleDragOver"
        @dragleave="handleDragLeave"
        @click="openFileDialog"
      >
        <div v-if="!originalImage" class="upload-placeholder">
          <div class="upload-icon">📁</div>
          <p>Drop your PNG image here or click to browse</p>
        </div>
        <img v-else :src="originalImage" alt="Original Image" class="uploaded-image" />
      </div>

      <!-- File Input (hidden) -->
      <input
        type="file"
        ref="fileInput"
        accept="image/*"
        @change="handleFileSelect"
        style="display: none"
      />

      <!-- Loading State -->
      <div v-if="loading" class="loading">
        <div class="spinner"></div>
        <p>Vectorizing your image...</p>
      </div>

      <!-- Results -->
      <div v-if="results && !loading" class="results">
        <h2>Vectorization Results</h2>

        <!-- Method Selection (Top Level) -->
        <div class="method-selection top-level">
          <h3>🚀 Vectorization Method</h3>
          <div class="method-buttons">
            <button
              @click="changeMethod('potrace')"
              :class="{ active: selectedMethod === 'potrace' }"
              class="method-btn">
              🖤 Potrace (Classic B&W)
            </button>
            <button
              @click="changeMethod('vtracer')"
              :class="{ active: selectedMethod === 'vtracer' }"
              class="method-btn">
              🌈 VTracer (Color Preserving)
            </button>
          </div>
        </div>

        <!-- Method Info -->
        <div class="method-info">
          <h3 v-if="selectedMethod === 'potrace'">🖤 Potrace Vectorizer</h3>
          <h3 v-if="selectedMethod === 'vtracer'">🌈 VTracer Vectorizer</h3>
          <p v-if="selectedMethod === 'potrace'">Professional bitmap tracing with advanced controls - converts to black & white</p>
          <p v-if="selectedMethod === 'vtracer'">Advanced color-preserving vectorization - maintains original image colors</p>
        </div>

        <!-- Parameters Panel -->
        <div class="parameters-panel">
          <button @click="showParameters = !showParameters" class="toggle-params-btn">
            {{ showParameters ? '🔧 Hide Parameters' : '🔧 Show Parameters' }}
          </button>

          <div v-if="showParameters" class="parameters-content">

            <!-- Potrace Parameters -->
            <div v-if="selectedMethod === 'potrace'" class="param-section">
              <h4>🎨 Potrace Controls</h4>

              <!-- Basic Controls -->
              <div class="param-group">
                <h5>Basic Settings</h5>
                <div class="param-row">
                  <label>
                    <input type="checkbox" v-model="parameters.potrace.invert" @change="reprocessImage">
                    Invert (for white text on black background)
                  </label>
                </div>
                <div class="param-row">
                  <label>Speckle Filter:</label>
                  <input type="range" min="0" max="20" v-model="parameters.potrace.turdsize" @input="debouncedReprocess" name="turdsize">
                  <span>{{ parameters.potrace.turdsize }}</span>
                </div>
              </div>

              <!-- Advanced Controls -->
              <div class="param-group">
                <h5>Advanced Settings</h5>
                <div class="param-row">
                  <label>Turn Policy:</label>
                  <select v-model="parameters.potrace.turnpolicy" @change="reprocessImage">
                    <option value="black">Black (favor black)</option>
                    <option value="white">White (favor white)</option>
                    <option value="left">Left (turn left)</option>
                    <option value="right">Right (turn right)</option>
                    <option value="minority">Minority (less is more)</option>
                    <option value="majority">Majority (more is less)</option>
                  </select>
                </div>
                <div class="param-row">
                  <label>Corner Threshold:</label>
                  <input type="range" min="0" max="2" step="0.1" v-model="parameters.potrace.alphamax" @input="debouncedReprocess" name="alphamax">
                  <span>{{ parameters.potrace.alphamax }}</span>
                </div>
                <div class="param-row">
                  <label>
                    <input type="checkbox" v-model="parameters.potrace.opticurve" @change="reprocessImage">
                    Optimize Curves
                  </label>
                </div>
              </div>

            </div>

            <!-- VTracer Parameters -->
            <div v-if="selectedMethod === 'vtracer'" class="param-section">
              <h4>🌈 VTracer Controls (Color Preserving)</h4>

              <!-- Basic Controls -->
              <div class="param-group">
                <h5>Color & Quality Settings</h5>
                <div class="param-row">
                  <label>Color Mode:</label>
                  <select v-model="parameters.vtracer.colormode" @change="reprocessImage">
                    <option value="color">Color (preserve original colors)</option>
                    <option value="bw">Black & White</option>
                  </select>
                </div>
                <div class="param-row">
                  <label>Color Precision:</label>
                  <input type="range" min="1" max="8" v-model="parameters.vtracer.color_precision" @input="debouncedReprocess" name="color_precision">
                  <span>{{ parameters.vtracer.color_precision }} bits</span>
                </div>
                <div class="param-row">
                  <label>Filter Speckles:</label>
                  <input type="range" min="0" max="20" v-model="parameters.vtracer.filter_speckle" @input="debouncedReprocess" name="filter_speckle">
                  <span>{{ parameters.vtracer.filter_speckle }}px</span>
                </div>
              </div>

              <!-- Advanced Controls -->
              <div class="param-group">
                <h5>Advanced Settings</h5>
                <div class="param-row">
                  <label>Corner Threshold:</label>
                  <input type="range" min="0" max="180" v-model="parameters.vtracer.corner_threshold" @input="debouncedReprocess" name="corner_threshold">
                  <span>{{ parameters.vtracer.corner_threshold }}°</span>
                </div>
                <div class="param-row">
                  <label>Path Length:</label>
                  <input type="range" min="1" max="20" step="0.5" v-model="parameters.vtracer.length_threshold" @input="debouncedReprocess" name="length_threshold">
                  <span>{{ parameters.vtracer.length_threshold }}</span>
                </div>
                <div class="param-row">
                  <label>Max Iterations:</label>
                  <input type="range" min="1" max="50" v-model="parameters.vtracer.max_iterations" @input="debouncedReprocess" name="max_iterations">
                  <span>{{ parameters.vtracer.max_iterations }}</span>
                </div>
                <div class="param-row">
                  <label>Path Precision:</label>
                  <input type="range" min="1" max="10" v-model="parameters.vtracer.path_precision" @input="debouncedReprocess" name="path_precision">
                  <span>{{ parameters.vtracer.path_precision }}</span>
                </div>
              </div>

            </div>
          </div>
        </div>

        <!-- Comparison View -->
        <div class="comparison-container">
          <div class="comparison-view" ref="comparisonView">
            <!-- Original Image Layer -->
            <div class="image-layer original-layer">
              <img :src="originalImage" alt="Original" />
            </div>

            <!-- Vectorized Layer -->
            <div class="image-layer vector-layer" :style="{ clipPath: `inset(0 ${100 - sliderValue}% 0 0)` }">
              <div class="svg-container" :class="{ 'loading-svg': parameterLoading }">
                <div v-html="getCurrentSVG()" class="svg-content" :key="selectedMethod"></div>
                <div v-if="parameterLoading" class="svg-loading-overlay">
                  <div class="mini-spinner"></div>
                </div>
              </div>
            </div>

            <!-- Slider -->
            <div class="slider-container">
              <input
                type="range"
                min="0"
                max="100"
                v-model="sliderValue"
                class="comparison-slider"
              />
              <div class="slider-line" :style="{ left: sliderValue + '%' }"></div>
            </div>
          </div>

          <!-- Slider Label -->
          <div class="slider-label">
            <span>Original</span>
            <span>Vectorized ({{ selectedMethod === 'potrace' ? 'Potrace' : 'VTracer' }})</span>
          </div>
        </div>

        <!-- Download Button -->
        <div class="download-section">
          <button @click="downloadSVG" class="download-btn">
            📥 Download SVG
          </button>
        </div>
      </div>

      <!-- Error State -->
      <div v-if="error" class="error">
        <h3>⚠️ Error</h3>
        <p>{{ error }}</p>
      </div>
    </main>
  </div>
</template>

<script>
import axios from 'axios'

export default {
  name: 'App',
  data() {
    return {
      isDragOver: false,
      originalImage: null,
      originalFile: null,
      results: null,
      selectedMethod: 'potrace',
      sliderValue: 50,
      loading: false,
      error: null,
      showParameters: false,
      parameterLoading: false,
      parameterDebounceTimer: null,
      selectedMethod: 'potrace', // Default to Potrace
      parameters: {
        potrace: {
          invert: false,
          turdsize: 2,
          turnpolicy: 'minority',
          alphamax: 1.0,
          opticurve: true,
        },
        vtracer: {
          colormode: 'color',
          color_precision: 6,
          filter_speckle: 4,
          corner_threshold: 60,
          length_threshold: 4.0,
          max_iterations: 10,
          splice_threshold: 45,
          path_precision: 3
        }
      }
    }
  },
  methods: {
    handleDragOver(e) {
      e.preventDefault()
      this.isDragOver = true
    },

    handleDragLeave(e) {
      e.preventDefault()
      this.isDragOver = false
    },

    handleDrop(e) {
      e.preventDefault()
      this.isDragOver = false

      const files = e.dataTransfer.files
      if (files.length > 0) {
        this.processFile(files[0])
      }
    },

    openFileDialog() {
      this.$refs.fileInput.click()
    },

    handleFileSelect(e) {
      const files = e.target.files
      if (files.length > 0) {
        this.processFile(files[0])
      }
    },

    async processFile(file) {
      if (!file.type.startsWith('image/')) {
        this.error = 'Please select a valid image file'
        return
      }

      this.error = null
      this.loading = true
      this.results = null
      this.originalFile = file

      // Show original image immediately
      const reader = new FileReader()
      reader.onload = (e) => {
        this.originalImage = e.target.result
      }
      reader.readAsDataURL(file)

      await this.vectorizeWithCurrentParameters()
    },

    async vectorizeWithCurrentParameters(selectedMethodOnly = false) {
      if (!this.originalFile) return

      console.log(`DEBUG: Making API call - selectedMethodOnly: ${selectedMethodOnly}, method: ${this.selectedMethod}`)
      console.log('DEBUG: Parameters:', JSON.stringify(this.parameters, null, 2))

      try {
        const formData = new FormData()
        formData.append('file', this.originalFile)
        formData.append('parameters', JSON.stringify(this.parameters))

        // Send selected method for faster parameter updates
        if (selectedMethodOnly) {
          formData.append('selected_method', this.selectedMethod)
          console.log(`DEBUG: Sending selected_method: ${this.selectedMethod}`)
        }

        const response = await axios.post('http://localhost:8000/vectorize', formData, {
          headers: {
            'Content-Type': 'multipart/form-data'
          }
        })

        console.log('DEBUG: API Response status:', response.status)
        console.log('DEBUG: API Response data:', response.data)

        // Merge results when only processing selected method
        if (selectedMethodOnly && this.results) {
          console.log('DEBUG: Merging results for selected method only')
          // Merge the new results with existing results
          this.results = {
            ...this.results,
            vectorized: {
              ...this.results.vectorized,
              ...response.data.vectorized
            }
          }
        } else {
          console.log('DEBUG: Replacing all results (initial load or method toggle)')
          // Replace all results (initial load or method toggle)
          this.results = response.data
        }
        console.log('DEBUG: Final results:', this.results)
        // Don't override selectedMethod - keep the user's selection!

      } catch (err) {
        this.error = err.response?.data?.detail || 'Failed to vectorize image'
        console.error('Vectorization error:', err)
      } finally {
        this.loading = false
        this.parameterLoading = false
      }
    },

    debouncedReprocess() {
      console.log('DEBUG: debouncedReprocess called')
      // Clear any existing timer
      if (this.parameterDebounceTimer) {
        console.log('DEBUG: Clearing existing debounce timer')
        clearTimeout(this.parameterDebounceTimer)
      }

      // Set parameter loading state immediately for visual feedback
      this.parameterLoading = true

      // Debounce the actual API call
      this.parameterDebounceTimer = setTimeout(() => {
        console.log('DEBUG: Debounce timer expired, calling reprocessImage')
        // Only process if we're still in parameter loading state (not cancelled)
        if (this.parameterLoading) {
          this.reprocessImage()
        }
      }, 500) // 500ms debounce
    },

    changeMethod(newMethod) {
      if (this.selectedMethod === newMethod) return // No change needed

      console.log(`Switching from ${this.selectedMethod} to ${newMethod}`)
      this.selectedMethod = newMethod

      // Only reprocess if we have an image loaded
      if (this.originalFile && !this.loading) {
        this.reprocessImage()
      }
    },

    async reprocessImage() {
      console.log('DEBUG: reprocessImage called')
      if (!this.originalFile) {
        console.log('DEBUG: No original file, skipping reprocess')
        return
      }
      if (this.loading) {
        console.log('DEBUG: Already loading, skipping reprocess')
        return
      }

      try {
        console.log('DEBUG: Calling vectorizeWithCurrentParameters(true)')
        await this.vectorizeWithCurrentParameters(true) // Only process current method
      } finally {
        console.log('DEBUG: reprocessImage completed')
        this.parameterLoading = false
        this.parameterDebounceTimer = null
      }
    },

    getCurrentSVG() {
      console.log('DEBUG: getCurrentSVG called')
      if (!this.results) {
        console.log('DEBUG: No results available')
        return ''
      }

      const svg = this.results.vectorized[this.selectedMethod]
      console.log(`DEBUG: SVG for method ${this.selectedMethod}:`, typeof svg, svg ? svg.substring(0, 50) + '...' : 'null')

      if (!svg) {
        console.log('DEBUG: No SVG found for selected method')
        return ''
      }

      // Handle both string and object responses
      const svgContent = typeof svg === 'object' ? svg.svg : svg
      console.log('DEBUG: SVG content type:', typeof svgContent)

      const isValidSVG = typeof svgContent === 'string' && svgContent.trim().startsWith('<')
      console.log('DEBUG: Is valid SVG:', isValidSVG)

      return isValidSVG ? svgContent : ''
    },

    downloadSVG() {
      const svg = this.getCurrentSVG()
      if (!svg) return

      const blob = new Blob([svg], { type: 'image/svg+xml' })
      const url = URL.createObjectURL(blob)

      const link = document.createElement('a')
      link.href = url
      link.download = `vectorized-${this.selectedMethod}.svg`
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
      URL.revokeObjectURL(url)
    }
  },
  beforeUnmount() {
    // Clean up any pending timers
    if (this.parameterDebounceTimer) {
      clearTimeout(this.parameterDebounceTimer)
    }
  }
}
</script>

<style>
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  min-height: 100vh;
}

#app {
  min-height: 100vh;
  padding: 20px;
}

header {
  text-align: center;
  color: white;
  margin-bottom: 30px;
}

header h1 {
  font-size: 3em;
  margin-bottom: 10px;
  text-shadow: 2px 2px 4px rgba(0,0,0,0.3);
}

header p {
  font-size: 1.2em;
  opacity: 0.9;
}

main {
  max-width: 1200px;
  margin: 0 auto;
}

.upload-area {
  background: white;
  border: 3px dashed #ddd;
  border-radius: 20px;
  padding: 60px 20px;
  text-align: center;
  cursor: pointer;
  transition: all 0.3s ease;
  margin-bottom: 30px;
}

.upload-area:hover,
.upload-area.dragover {
  border-color: #667eea;
  background: #f8f9ff;
  transform: translateY(-2px);
}

.upload-placeholder {
  color: #666;
}

.upload-icon {
  font-size: 4em;
  margin-bottom: 20px;
}

.uploaded-image {
  max-width: 100%;
  max-height: 300px;
  border-radius: 10px;
  box-shadow: 0 10px 30px rgba(0,0,0,0.2);
}

.loading {
  text-align: center;
  color: white;
  padding: 40px;
}

.spinner {
  width: 40px;
  height: 40px;
  border: 4px solid rgba(255,255,255,0.3);
  border-top: 4px solid white;
  border-radius: 50%;
  animation: spin 1s linear infinite;
  margin: 0 auto 20px;
}

@keyframes spin {
  0% { transform: rotate(0deg); }
  100% { transform: rotate(360deg); }
}

.results {
  background: white;
  border-radius: 20px;
  padding: 30px;
  box-shadow: 0 20px 60px rgba(0,0,0,0.2);
}

.results h2 {
  color: #333;
  margin-bottom: 20px;
  text-align: center;
}

.method-selector {
  margin-bottom: 30px;
  text-align: center;
}

.method-selector label {
  display: inline-block;
  margin-right: 10px;
  font-weight: bold;
  color: #555;
}

.method-selector select {
  padding: 10px 15px;
  border: 2px solid #ddd;
  border-radius: 10px;
  font-size: 16px;
  background: white;
}

.comparison-container {
  margin-bottom: 30px;
}

.comparison-view {
  position: relative;
  width: 100%;
  height: 500px;
  border-radius: 15px;
  overflow: hidden;
  box-shadow: 0 10px 30px rgba(0,0,0,0.2);
}

.image-layer {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}

.original-layer img {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
}

.vector-layer {
  background: white;
}

/* SVG container styles moved to bottom of file */

.slider-container {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
}

.comparison-slider {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  opacity: 0;
  pointer-events: all;
  cursor: ew-resize;
}

.slider-line {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 4px;
  background: #667eea;
  box-shadow: 0 0 10px rgba(102, 126, 234, 0.5);
  pointer-events: none;
  transform: translateX(-2px);
}

.slider-line::before {
  content: '';
  position: absolute;
  top: 50%;
  left: 50%;
  width: 20px;
  height: 20px;
  background: #667eea;
  border: 3px solid white;
  border-radius: 50%;
  transform: translate(-50%, -50%);
  box-shadow: 0 2px 10px rgba(0,0,0,0.2);
}

.slider-label {
  display: flex;
  justify-content: space-between;
  margin-top: 15px;
  padding: 0 20px;
  font-size: 14px;
  color: #666;
  font-weight: bold;
}

.download-section {
  text-align: center;
}

.download-btn {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  border: none;
  padding: 15px 30px;
  border-radius: 50px;
  font-size: 16px;
  font-weight: bold;
  cursor: pointer;
  transition: transform 0.2s ease;
}

.download-btn:hover {
  transform: translateY(-2px);
}

.error {
  background: #ff6b6b;
  color: white;
  padding: 20px;
  border-radius: 10px;
  text-align: center;
  margin-top: 20px;
}

.error h3 {
  margin-bottom: 10px;
}

/* Parameters Panel */
.parameters-panel {
  margin-bottom: 30px;
  background: #f8f9ff;
  border-radius: 15px;
  overflow: hidden;
}

.toggle-params-btn {
  width: 100%;
  background: #667eea;
  color: white;
  border: none;
  padding: 15px 20px;
  font-size: 16px;
  font-weight: bold;
  cursor: pointer;
  transition: background 0.2s ease;
}

.toggle-params-btn:hover {
  background: #5a67d8;
}

.parameters-content {
  padding: 20px;
  background: white;
}

.param-section {
  background: #f8f9ff;
  border-radius: 10px;
  padding: 15px;
}

.param-section h4 {
  color: #4a5568;
  margin-bottom: 15px;
  font-size: 18px;
}

.param-row {
  display: flex;
  align-items: center;
  margin-bottom: 15px;
  gap: 10px;
}

.param-row:last-child {
  margin-bottom: 0;
}

.param-row label {
  min-width: 120px;
  font-weight: 500;
  color: #4a5568;
  display: flex;
  align-items: center;
  gap: 5px;
}

.param-row input[type="range"] {
  flex: 1;
  height: 6px;
  background: #e2e8f0;
  border-radius: 3px;
  outline: none;
  -webkit-appearance: none;
  margin: 0 10px;
}

.param-row input[type="range"]::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: 18px;
  height: 18px;
  background: #667eea;
  border-radius: 50%;
  cursor: pointer;
}

.param-row input[type="range"]::-moz-range-thumb {
  width: 18px;
  height: 18px;
  background: #667eea;
  border-radius: 50%;
  cursor: pointer;
  border: none;
}

.param-row select {
  flex: 1;
  padding: 8px 12px;
  border: 2px solid #e2e8f0;
  border-radius: 8px;
  font-size: 14px;
  background: white;
  margin-left: 10px;
}

.param-row span {
  min-width: 40px;
  text-align: right;
  font-weight: bold;
  color: #4a5568;
}

.param-row input[type="checkbox"] {
  margin-right: 8px;
  transform: scale(1.2);
}

/* Loading overlay for parameter changes */
.results.loading-params {
  opacity: 0.7;
  pointer-events: none;
}

/* Method Info */
.method-info {
  text-align: center;
  margin-bottom: 20px;
  padding: 20px;
  background: linear-gradient(135deg, #f8f9ff 0%, #e8f0ff 100%);
  border-radius: 15px;
  border: 1px solid #667eea;
}

.method-info h3 {
  color: #4a5568;
  margin-bottom: 8px;
  font-size: 24px;
}

.method-info p {
  color: #6b7280;
  font-size: 16px;
}

/* Parameter Groups */
.param-group {
  margin-bottom: 25px;
  padding: 15px;
  background: #f8f9ff;
  border-radius: 8px;
  border-left: 4px solid #667eea;
}

.param-group h5 {
  color: #4a5568;
  margin-bottom: 15px;
  font-size: 16px;
  font-weight: 600;
  border-bottom: 1px solid #e2e8f0;
  padding-bottom: 5px;
}

/* Color Inputs */
.color-input {
  width: 50px;
  height: 35px;
  border: 2px solid #e2e8f0;
  border-radius: 8px;
  cursor: pointer;
  margin: 0 10px;
}

.color-input:hover {
  border-color: #667eea;
}

/* SVG Container and Loading States */
.svg-container {
  position: relative;
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}

.svg-content {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: opacity 0.2s ease;
}

.svg-container.loading-svg .svg-content {
  opacity: 0.3;
}

.svg-loading-overlay {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(255, 255, 255, 0.8);
  z-index: 10;
}

.mini-spinner {
  width: 24px;
  height: 24px;
  border: 3px solid rgba(102, 126, 234, 0.3);
  border-top: 3px solid #667eea;
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

/* Ensure SVG sizing consistency */
.svg-content svg {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
}

/* Improve comparison layer alignment */
.original-layer,
.vector-layer {
  background-size: contain;
  background-repeat: no-repeat;
  background-position: center;
}

.original-layer img {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  display: block;
}

/* Stable container for better performance */
.comparison-view {
  position: relative;
  width: 100%;
  height: 500px;
  border-radius: 15px;
  overflow: hidden;
  box-shadow: 0 10px 30px rgba(0,0,0,0.2);
  contain: layout style paint;
}

.method-selection {
  background: #f0f4f8;
  border-radius: 10px;
  padding: 20px;
  margin-bottom: 20px;
  border: 2px solid #e2e8f0;
}

.method-selection.top-level {
  background: linear-gradient(135deg, #f0f4f8 0%, #e6f3ff 100%);
  border: 3px solid #667eea;
  margin-bottom: 30px;
  padding: 25px;
}

.method-selection.top-level h3 {
  color: #2d3748;
  font-size: 20px;
  font-weight: 700;
  margin-bottom: 20px;
  text-align: center;
}

.method-selection h4 {
  color: #2d3748;
  margin-bottom: 15px;
  font-size: 18px;
  font-weight: 600;
}

.method-buttons {
  display: flex;
  gap: 10px;
}

.method-btn {
  background: white;
  border: 2px solid #e2e8f0;
  color: #4a5568;
  padding: 12px 20px;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.3s ease;
  flex: 1;
}

.method-btn:hover {
  border-color: #667eea;
  transform: translateY(-1px);
}

.method-btn.active {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border-color: #667eea;
  color: white;
  box-shadow: 0 4px 15px rgba(102, 126, 234, 0.3);
}
</style>