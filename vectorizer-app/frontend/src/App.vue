<template>
  <div id="app">
    <!-- Header -->
    <header class="header">
      <div class="header-content">
        <div class="brand">
          <div class="brand-icon">
            <svg width="32" height="32" viewBox="0 0 32 32" fill="none">
              <path d="M16 3L28 9V23L16 29L4 23V9L16 3Z" stroke="currentColor" stroke-width="2" fill="none"/>
              <path d="M16 16L22 12V20L16 16Z" fill="currentColor"/>
            </svg>
          </div>
          <h1>Vectorizer</h1>
        </div>
        <p class="subtitle">Transform images into scalable vectors</p>
      </div>
    </header>

    <main class="main">
      <!-- Upload Section -->
      <section class="upload-section">
        <div
          class="upload-zone"
          :class="{ 'drag-active': isDragOver, 'has-image': originalImage }"
          @drop="handleDrop"
          @dragover="handleDragOver"
          @dragleave="handleDragLeave"
          @click="openFileDialog"
        >
          <div v-if="!originalImage" class="upload-content">
            <div class="upload-icon">
              <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                <polyline points="14,2 14,8 20,8"/>
                <line x1="16" y1="13" x2="8" y2="13"/>
                <line x1="16" y1="17" x2="8" y2="17"/>
                <polyline points="10,9 9,9 8,9"/>
              </svg>
            </div>
            <h3>Drop your image</h3>
            <p>or click to browse files</p>
            <div class="supported-formats">
              <span>PNG</span>
              <span>JPG</span>
              <span>GIF</span>
            </div>
            <p class="size-limit">Maximum file size: 20MB</p>
          </div>
          <div v-else class="uploaded-preview">
            <img :src="originalImage" alt="Original Image" />
            <div class="image-overlay">
              <button @click.stop="removeImage" class="remove-btn">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <line x1="18" y1="6" x2="6" y2="18"></line>
                  <line x1="6" y1="6" x2="18" y2="18"></line>
                </svg>
              </button>
            </div>
          </div>
        </div>
      </section>

      <!-- Hidden file input -->
      <input
        type="file"
        ref="fileInput"
        accept="image/png,image/jpeg,image/gif"
        @change="handleFileSelect"
        class="hidden-input"
      />

      <!-- Loading State -->
      <div v-if="loading" class="loading-state">
        <div class="loading-spinner"></div>
        <p>Processing your image...</p>
      </div>

      <!-- Results Section -->
      <section v-if="results && !loading" class="results-section">
        <!-- Method Selection -->
        <div class="method-selector">
          <h2>Choose Method</h2>
          <div class="method-tabs">
            <button
              @click="changeMethod('vtracer')"
              :class="{ active: selectedMethod === 'vtracer' }"
              class="method-tab"
            >
              <div class="method-icon">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <circle cx="12" cy="12" r="10"/>
                  <path d="M8 12h8"/>
                  <path d="M12 8v8"/>
                  <path d="M16 8h-8l4 8 4-8z" fill="currentColor" opacity="0.3"/>
                </svg>
              </div>
              <div class="method-info">
                <h3>VTracer</h3>
                <p>Color preserving</p>
              </div>
            </button>
            <button
              @click="changeMethod('potrace')"
              :class="{ active: selectedMethod === 'potrace' }"
              class="method-tab"
            >
              <div class="method-icon">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
                  <circle cx="9" cy="9" r="2"/>
                  <path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/>
                </svg>
              </div>
              <div class="method-info">
                <h3>Potrace</h3>
                <p>Classic black & white</p>
              </div>
            </button>
          </div>
        </div>

        <!-- Parameters Panel -->
        <div class="controls-panel">
          <button
            @click="showParameters = !showParameters"
            class="controls-toggle"
            :class="{ 'expanded': showParameters }"
          >
            <span>Advanced Settings</span>
            <svg class="chevron" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="6,9 12,15 18,9"></polyline>
            </svg>
          </button>

          <div v-if="showParameters" class="controls-content">
            <!-- VTracer Controls -->
            <div v-if="selectedMethod === 'vtracer'" class="control-group">
              <div class="control-section">
                <h4>Color & Quality</h4>
                <div class="control-grid">
                  <div class="control-item">
                    <label>Color Mode</label>
                    <select v-model="parameters.vtracer.colormode" @change="reprocessImage">
                      <option value="color">Color</option>
                      <option value="binary">Binary</option>
                    </select>
                  </div>
                  <div class="control-item">
                    <label>Color Precision</label>
                    <div class="slider-control">
                      <input
                        type="range"
                        min="1"
                        max="8"
                        v-model="parameters.vtracer.color_precision"
                        @input="debouncedReprocess"
                        class="slider"
                      />
                      <span class="value">{{ parameters.vtracer.color_precision }}</span>
                    </div>
                  </div>
                  <div class="control-item">
                    <label>Filter Speckle</label>
                    <div class="slider-control">
                      <input
                        type="range"
                        min="1"
                        max="100"
                        v-model="parameters.vtracer.filter_speckle"
                        @input="debouncedReprocess"
                        class="slider"
                      />
                      <span class="value">{{ parameters.vtracer.filter_speckle }}</span>
                    </div>
                  </div>
                </div>
              </div>

              <div class="control-section">
                <h4>Path Settings</h4>
                <div class="control-grid">
                  <div class="control-item">
                    <label>Corner Threshold</label>
                    <div class="slider-control">
                      <input
                        type="range"
                        min="0"
                        max="180"
                        v-model="parameters.vtracer.corner_threshold"
                        @input="debouncedReprocess"
                        class="slider"
                      />
                      <span class="value">{{ parameters.vtracer.corner_threshold }}°</span>
                    </div>
                  </div>
                  <div class="control-item">
                    <label>Path Precision</label>
                    <div class="slider-control">
                      <input
                        type="range"
                        min="1"
                        max="10"
                        v-model="parameters.vtracer.path_precision"
                        @input="debouncedReprocess"
                        class="slider"
                      />
                      <span class="value">{{ parameters.vtracer.path_precision }}</span>
                    </div>
                  </div>
                  <div class="control-item">
                    <label>Max Iterations</label>
                    <div class="slider-control">
                      <input
                        type="range"
                        min="1"
                        max="100"
                        v-model="parameters.vtracer.max_iterations"
                        @input="debouncedReprocess"
                        class="slider"
                      />
                      <span class="value">{{ parameters.vtracer.max_iterations }}</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <!-- Potrace Controls -->
            <div v-if="selectedMethod === 'potrace'" class="control-group">
              <div class="control-section">
                <h4>Basic Settings</h4>
                <div class="control-grid">
                  <div class="control-item checkbox-item">
                    <label class="checkbox-label">
                      <input type="checkbox" v-model="parameters.potrace.invert" @change="reprocessImage" />
                      <span class="checkmark"></span>
                      Invert Colors
                    </label>
                  </div>
                  <div class="control-item checkbox-item">
                    <label class="checkbox-label">
                      <input type="checkbox" v-model="parameters.potrace.opticurve" @change="reprocessImage" />
                      <span class="checkmark"></span>
                      Curve Optimization
                    </label>
                  </div>
                </div>
              </div>

              <div class="control-section">
                <h4>Advanced Options</h4>
                <div class="control-grid">
                  <div class="control-item">
                    <label>Speckle Filter</label>
                    <div class="slider-control">
                      <input
                        type="range"
                        min="0"
                        max="20"
                        v-model="parameters.potrace.turdsize"
                        @input="debouncedReprocess"
                        class="slider"
                      />
                      <span class="value">{{ parameters.potrace.turdsize }}</span>
                    </div>
                  </div>
                  <div class="control-item">
                    <label>Turn Policy</label>
                    <select v-model="parameters.potrace.turnpolicy" @change="reprocessImage">
                      <option value="minority">Minority</option>
                      <option value="majority">Majority</option>
                      <option value="black">Black</option>
                      <option value="white">White</option>
                      <option value="left">Left</option>
                      <option value="right">Right</option>
                      <option value="random">Random</option>
                    </select>
                  </div>
                  <div class="control-item">
                    <label>Alpha Max</label>
                    <div class="slider-control">
                      <input
                        type="range"
                        min="0"
                        max="2"
                        step="0.1"
                        v-model="parameters.potrace.alphamax"
                        @input="debouncedReprocess"
                        class="slider"
                      />
                      <span class="value">{{ parameters.potrace.alphamax }}</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- Comparison View -->
        <div class="comparison-container">
          <div class="comparison-view">
            <div class="image-comparison">
              <!-- Original Image Layer (Full Width Background) -->
              <div class="image-layer original-layer">
                <img :src="originalImage" alt="Original" />
              </div>

              <!-- Vector overlay is rendered only inside the mask below -->

              <!-- Clipping Mask for Vector Side -->
              <div class="vector-mask" :style="{ clipPath: `polygon(${sliderValue}% 0%, 100% 0%, 100% 100%, ${sliderValue}% 100%)` }">
                <div class="svg-container" :key="selectedMethod + '-mask'">
                  <div v-html="getCurrentSVG()" class="svg-content"></div>
                </div>
              </div>

              <!-- Side Labels -->
              <div class="side-label left-label">Original</div>
              <div class="side-label right-label">{{ selectedMethod === 'vtracer' ? 'VTracer' : 'Potrace' }}</div>

              <!-- Slider -->
              <div class="slider-container">
                <input
                  type="range"
                  min="0"
                  max="100"
                  v-model="sliderValue"
                  class="comparison-slider"
                />
                <div class="slider-handle" :style="{ left: sliderValue + '%' }">
                  <div class="handle-icon">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3">
                      <polyline points="9,18 15,12 9,6"></polyline>
                    </svg>
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3">
                      <polyline points="15,18 9,12 15,6"></polyline>
                    </svg>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- Actions -->
        <div class="actions">
          <button @click="downloadSVG" class="action-btn primary">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
              <polyline points="7,10 12,15 17,10"/>
              <line x1="12" y1="15" x2="12" y2="3"/>
            </svg>
            Download SVG
          </button>
          <button @click="processNewImage" class="action-btn secondary">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/>
              <path d="M21 3v5h-5"/>
              <path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/>
              <path d="M8 16l-5 5v-5h5"/>
            </svg>
            New Image
          </button>
        </div>
      </section>

      <!-- Error State -->
      <div v-if="error" class="error-state">
        <div class="error-icon">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="12" cy="12" r="10"/>
            <line x1="15" y1="9" x2="9" y2="15"/>
            <line x1="9" y1="9" x2="15" y2="15"/>
          </svg>
        </div>
        <h3>Something went wrong</h3>
        <p>{{ error }}</p>
      </div>
    </main>
  </div>
</template>

<script>
import axios from 'axios'

export default {
  name: 'App',
  mounted() {
    // Display ASCII art in console
    this.showConsoleArt()
  },
  data() {
    return {
      isDragOver: false,
      originalImage: null,
      originalFile: null,
      results: null,
      selectedMethod: 'vtracer',
      sliderValue: 50,
      loading: false,
      error: null,
      showParameters: false,
      parameterLoading: false,
      parameterDebounceTimer: null,
      selectedMethod: 'vtracer', // Default to VTracer
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
    showConsoleArt() {
      const styles = 'color: #718096; font-weight: bold; font-size: 14px; line-height: 1.2;'
      const titleStyle = 'color: #4a5568; font-weight: bold; font-size: 16px;'
      const subtitleStyle = 'color: #a0aec0; font-size: 12px;'

      console.log('%c' + `
╭─────────────────────────────────────────────────────────────╮
│    ██╗   ██╗███████╗ ██████╗████████╗ ██████╗ ██████╗       │
│    ██║   ██║██╔════╝██╔════╝╚══██╔══╝██╔═══██╗██╔══██╗      │
│    ██║   ██║█████╗  ██║        ██║   ██║   ██║██████╔╝      │
│    ╚██╗ ██╔╝██╔══╝  ██║        ██║   ██║   ██║██╔══██╗      │
│     ╚████╔╝ ███████╗╚██████╗   ██║   ╚██████╔╝██║  ██║      │
│      ╚═══╝  ╚══════╝ ╚═════╝   ╚═╝    ╚═════╝ ╚═╝  ╚═╝      │
│                                                             │
│               ██╗███████╗███████╗██████╗                    │
│               ██║╚══███╔╝██╔════╝██╔══██╗                   │
│               ██║  ███╔╝ █████╗  ██████╔╝                   │
│               ██║ ███╔╝  ██╔══╝  ██╔══██╗                   │
│               ██║███████╗███████╗██║  ██║                   │
│               ╚═╝╚══════╝╚══════╝╚═╝  ╚═╝                   │
╰─────────────────────────────────────────────────────────────╯`, styles)

      console.log('%cTransform Images into Scalable Vectors', titleStyle)
      console.log('%cPowered by VTracer & Potrace • Built with Vue.js & FastAPI', subtitleStyle)
      console.log('')
      console.log('%c🎨 Upload PNG, JPEG, or GIF images up to 20MB', subtitleStyle)
      console.log('%c⚡ Real-time parameter tuning with instant preview', subtitleStyle)
      console.log('%c📱 PWA-enabled for offline use', subtitleStyle)
      console.log('')
      console.log('%cDeveloper Console Ready ✨', 'color: #4a5568; font-weight: bold;')
    },
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

    removeImage() {
      this.originalImage = null
      this.originalFile = null
      this.results = null
      this.error = null
      this.sliderValue = 50  // Reset comparison slider
      this.loading = false
      this.parameterLoading = false
      this.showParameters = false  // Close parameters panel

      // Clear any pending parameter debounce timers
      if (this.parameterDebounceTimer) {
        clearTimeout(this.parameterDebounceTimer)
        this.parameterDebounceTimer = null
      }

      // Reset file input
      if (this.$refs.fileInput) {
        this.$refs.fileInput.value = ''
      }
    },

    processNewImage() {
      this.removeImage()
      this.openFileDialog()
    },

    async processFile(file) {
      // Validate file type
      const allowedTypes = ['image/png', 'image/jpeg', 'image/gif']
      if (!allowedTypes.includes(file.type)) {
        this.error = 'Please select a PNG, JPEG, or GIF image file'
        return
      }

      // Validate file size (20MB limit)
      const maxSize = 20 * 1024 * 1024 // 20MB in bytes
      if (file.size > maxSize) {
        this.error = 'File size exceeds 20MB limit. Please choose a smaller image.'
        return
      }

      this.loading = true
      this.error = null
      this.originalFile = file

      // Create preview
      const reader = new FileReader()
      reader.onload = (e) => {
        this.originalImage = e.target.result
      }
      reader.readAsDataURL(file)

      await this.vectorizeWithCurrentParameters()
    },

    async vectorizeWithCurrentParameters(selectedMethodOnly = false) {

      if (!this.originalFile) {
        
        return
      }

      try {
        this.loading = !selectedMethodOnly
        this.parameterLoading = selectedMethodOnly

        const formData = new FormData()
        formData.append('file', this.originalFile)
        formData.append('parameters', JSON.stringify(this.parameters))
        formData.append('selected_method', selectedMethodOnly ? this.selectedMethod : '')

        

        const apiBase = import.meta.env.VITE_API_URL || 'http://localhost:8000'
        const response = await axios.post(`${apiBase}/vectorize`, formData, {
          headers: {
            'Content-Type': 'multipart/form-data'
          }
        })

        

        if (selectedMethodOnly && this.results) {
          // Merge results to preserve other method's results
          this.results = {
            ...this.results,
            vectorized: {
              ...this.results.vectorized,
              ...response.data.vectorized
            }
          }
          
        } else {
          this.results = response.data
          
        }

        this.error = null
      } catch (error) {
        
        if (error.response) {
          this.error = error.response.data?.detail || 'Processing failed'
        } else if (error.request) {
          this.error = 'Server not responding. Please check if the backend is running.'
        } else {
          this.error = 'An unexpected error occurred'
        }
      } finally {
        this.loading = false
        this.parameterLoading = false
      }
    },

    debouncedReprocess() {
      

      if (this.parameterDebounceTimer) {
        clearTimeout(this.parameterDebounceTimer)
      }

      this.parameterLoading = true

      this.parameterDebounceTimer = setTimeout(() => {
        
        if (this.parameterLoading) {
          this.reprocessImage()
        }
      }, 500) // 500ms debounce
    },

    changeMethod(newMethod) {
      if (this.selectedMethod === newMethod) return // No change needed

      
      this.selectedMethod = newMethod

      // Only reprocess if we have an image loaded
      if (this.originalFile && !this.loading) {
        this.reprocessImage()
      }
    },

    async reprocessImage() {
      
      if (!this.originalFile) {
        
        return
      }
      if (this.loading) {
        
        return
      }

      try {
        
        await this.vectorizeWithCurrentParameters(true) // Only process current method
      } finally {
        
        this.parameterLoading = false
        this.parameterDebounceTimer = null
      }
    },

    getCurrentSVG() {
      
      if (!this.results) {
        
        return ''
      }

      const svg = this.results.vectorized[this.selectedMethod]
      

      if (!svg) {
        
        return '<div style="display: flex; align-items: center; justify-content: center; height: 100%; color: #666;">Processing...</div>'
      }

      if (svg.startsWith('Error:')) {
        
        return `<div style="display: flex; align-items: center; justify-content: center; height: 100%; color: #ff6b6b; text-align: center; padding: 20px;">${svg}</div>`
      }

      return svg
    },

    downloadSVG() {
      const svg = this.getCurrentSVG()
      if (!svg || svg.includes('Error:')) {
        this.error = 'No valid SVG to download'
        return
      }

      const blob = new Blob([svg], { type: 'image/svg+xml' })
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `vectorized-${this.selectedMethod}-${Date.now()}.svg`
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
      URL.revokeObjectURL(url)
    }
  }
}
</script>

<style>
/* Reset and Base Styles */
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', 'Helvetica', 'Arial', sans-serif;
  font-size: 16px;
  line-height: 1.5;
  background: #fafbfc;
  min-height: 100vh;
  color: #4a5568;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

#app {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}

/* Header */
.header {
  padding: 3rem 0 2rem;
  text-align: center;
}

.header-content {
  max-width: 1200px;
  margin: 0 auto;
  padding: 0 2rem;
}

.brand {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 1rem;
  margin-bottom: 0.75rem;
}

.brand-icon {
  width: 2.5rem;
  height: 2.5rem;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  color: #718096;
}

.brand h1 {
  font-size: 2.25rem;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: #1a202c;
}

.subtitle {
  font-size: 1rem;
  color: #64748b;
  font-weight: 400;
}

/* Main Content */
.main {
  flex: 1;
  max-width: 1200px;
  margin: 0 auto;
  padding: 0 2rem 2rem;
  width: 100%;
}

/* Upload Section */
.upload-section {
  margin-bottom: 2rem;
}

.upload-zone {
  background: white;
  border: 2px dashed #cbd5e0;
  border-radius: 12px;
  padding: 3rem 2rem;
  text-align: center;
  cursor: pointer;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
}

.upload-zone:hover,
.upload-zone.drag-active {
  border-color: #4a5568;
  transform: translateY(-1px);
  box-shadow: 0 4px 16px rgba(74, 85, 104, 0.1);
  background: #f7fafc;
}

.upload-content {
  position: relative;
  z-index: 1;
}

.upload-icon {
  width: 3rem;
  height: 3rem;
  margin: 0 auto 1rem;
  color: #a0aec0;
  transition: color 0.3s ease;
}

.upload-zone:hover .upload-icon {
  color: #4a5568;
}

.upload-content h3 {
  font-size: 1.125rem;
  font-weight: 600;
  margin-bottom: 0.5rem;
  color: #2d3748;
}

.upload-content p {
  color: #718096;
  margin-bottom: 1.25rem;
  font-size: 0.9375rem;
}

.supported-formats {
  display: flex;
  gap: 0.5rem;
  justify-content: center;
}

.supported-formats span {
  background: #edf2f7;
  color: #718096;
  padding: 0.25rem 0.625rem;
  border-radius: 4px;
  font-size: 0.8125rem;
  font-weight: 500;
}

.size-limit {
  font-size: 0.8125rem;
  color: #a0aec0;
  margin-top: 0.75rem;
  font-weight: 400;
}

.uploaded-preview {
  position: relative;
  display: inline-block;
}

.uploaded-preview img {
  max-width: 100%;
  max-height: 300px;
  border-radius: 12px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.12);
}

.image-overlay {
  position: absolute;
  top: 8px;
  right: 8px;
}

.remove-btn {
  width: 32px;
  height: 32px;
  background: rgba(0, 0, 0, 0.7);
  border: none;
  border-radius: 50%;
  color: white;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.2s ease;
}

.remove-btn:hover {
  background: rgba(0, 0, 0, 0.9);
}

.hidden-input {
  display: none;
}

/* Loading State */
.loading-state {
  text-align: center;
  color: #4a5568;
  padding: 3rem 2rem;
  background: white;
  border-radius: 12px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.05);
}

.loading-spinner {
  width: 36px;
  height: 36px;
  border: 3px solid #e2e8f0;
  border-top: 3px solid #4a5568;
  border-radius: 50%;
  animation: spin 1s linear infinite;
  margin: 0 auto 1rem;
}

@keyframes spin {
  0% { transform: rotate(0deg); }
  100% { transform: rotate(360deg); }
}

/* Results Section */
.results-section {
  background: white;
  border-radius: 12px;
  padding: 2rem;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.05);
  border: 1px solid #e2e8f0;
}

/* Method Selector */
.method-selector {
  margin-bottom: 2rem;
}

.method-selector h2 {
  font-size: 1.375rem;
  font-weight: 600;
  margin-bottom: 1rem;
  color: #2d3748;
}

.method-tabs {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.75rem;
}

.method-tab {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 1.25rem;
  cursor: pointer;
  transition: all 0.2s ease;
  display: flex;
  align-items: center;
  gap: 0.875rem;
  text-align: left;
}

.method-tab:hover {
  border-color: #4a5568;
  background: #f1f5f9;
  box-shadow: 0 2px 8px rgba(74, 85, 104, 0.1);
}

.method-tab.active {
  border-color: #4a5568;
  background: #4a5568;
  color: white;
  box-shadow: 0 3px 12px rgba(74, 85, 104, 0.2);
}

.method-icon {
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #e2e8f0;
  border-radius: 6px;
  flex-shrink: 0;
  color: #4a5568;
}

.method-tab.active .method-icon {
  background: rgba(255, 255, 255, 0.2);
  color: white;
}

.method-info h3 {
  font-size: 1.125rem;
  font-weight: 600;
  margin-bottom: 0.25rem;
}

.method-info p {
  font-size: 0.875rem;
  opacity: 0.7;
}

/* Controls Panel */
.controls-panel {
  margin-bottom: 2rem;
  background: #f8fafc;
  border-radius: 8px;
  overflow: hidden;
  border: 1px solid #e2e8f0;
}

.controls-toggle {
  width: 100%;
  background: white;
  border: none;
  padding: 1rem 1.25rem;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 0.9375rem;
  font-weight: 500;
  color: #2d3748;
  transition: all 0.2s ease;
}

.controls-toggle:hover {
  background: #f8fafc;
}

.controls-toggle.expanded {
  background: #f1f5f9;
}

.chevron {
  transition: transform 0.3s ease;
}

.controls-toggle.expanded .chevron {
  transform: rotate(180deg);
}

.controls-content {
  padding: 1.5rem;
  background: white;
  border-top: 1px solid #e5e7eb;
}

.control-group {
  display: flex;
  flex-direction: column;
  gap: 2rem;
}

.control-section h4 {
  font-size: 1rem;
  font-weight: 600;
  margin-bottom: 0.875rem;
  color: #2d3748;
  border-bottom: 1px solid #e2e8f0;
  padding-bottom: 0.375rem;
}

.control-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 1.25rem;
}

.control-item {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.control-item label {
  font-size: 0.8125rem;
  font-weight: 500;
  color: #4a5568;
}

.control-item select {
  padding: 0.75rem 1rem;
  border: 1px solid #d1d5db;
  border-radius: 8px;
  background: white;
  font-size: 0.875rem;
  transition: border-color 0.2s ease;
}

.control-item select:focus {
  outline: none;
  border-color: #4a5568;
  box-shadow: 0 0 0 3px rgba(74, 85, 104, 0.1);
}

.slider-control {
  display: flex;
  align-items: center;
  gap: 0.875rem;
}

.slider {
  flex: 1;
  height: 4px;
  background: #e2e8f0;
  border-radius: 2px;
  outline: none;
  -webkit-appearance: none;
  cursor: pointer;
}

.slider::-webkit-slider-thumb {
  -webkit-appearance: none;
  width: 16px;
  height: 16px;
  background: #4a5568;
  border-radius: 50%;
  cursor: pointer;
  box-shadow: 0 1px 4px rgba(74, 85, 104, 0.3);
  transition: transform 0.2s ease;
}

.slider::-webkit-slider-thumb:hover {
  transform: scale(1.125);
}

.slider::-moz-range-thumb {
  width: 16px;
  height: 16px;
  background: #4a5568;
  border-radius: 50%;
  cursor: pointer;
  border: none;
  box-shadow: 0 1px 4px rgba(74, 85, 104, 0.3);
}

.value {
  font-weight: 500;
  color: #4a5568;
  min-width: 36px;
  text-align: right;
  font-size: 0.8125rem;
}

.checkbox-item {
  flex-direction: row;
  align-items: center;
}

.checkbox-label {
  display: flex !important;
  align-items: center;
  gap: 0.75rem;
  cursor: pointer;
  font-weight: 500 !important;
}

.checkbox-label input[type="checkbox"] {
  display: none;
}

.checkmark {
  width: 18px;
  height: 18px;
  border: 1px solid #cbd5e0;
  border-radius: 3px;
  position: relative;
  transition: all 0.2s ease;
}

.checkbox-label input[type="checkbox"]:checked + .checkmark {
  background: #4a5568;
  border-color: #4a5568;
}

.checkbox-label input[type="checkbox"]:checked + .checkmark::after {
  content: '';
  position: absolute;
  top: 2px;
  left: 6px;
  width: 4px;
  height: 8px;
  border: solid white;
  border-width: 0 2px 2px 0;
  transform: rotate(45deg);
}

/* Comparison Container */
.comparison-container {
  margin-bottom: 2rem;
}

.comparison-view {
  position: relative;
  height: 500px;
  border-radius: 16px;
  overflow: hidden;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.12);
  background: white;
}

.image-comparison {
  position: relative;
  width: 100%;
  height: 100%;
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
  overflow: hidden;
}

.original-layer {
  background: #f9fafb;
}

.vector-layer {
  background: white;
}

.vector-mask {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  background: white;
  z-index: 2;
}

.original-layer img {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
}

.svg-container {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}

.svg-content {
  max-width: 100%;
  max-height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}

.svg-content svg {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
}

.side-label {
  position: absolute;
  bottom: 12px;
  background: rgba(0, 0, 0, 0.7);
  color: white;
  padding: 0.5rem 0.75rem;
  border-radius: 6px;
  font-size: 0.875rem;
  font-weight: 600;
  backdrop-filter: blur(4px);
  z-index: 10;
}

.left-label {
  left: 12px;
}

.right-label {
  right: 12px;
}

.slider-container {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
  z-index: 20;
}

.comparison-slider {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  opacity: 0;
  cursor: ew-resize;
  pointer-events: all;
}

.slider-handle {
  position: absolute;
  top: 0;
  height: 100%;
  width: 2px;
  background: #4a5568;
  transform: translateX(-1px);
  pointer-events: none;
  box-shadow: 0 0 12px rgba(74, 85, 104, 0.3);
}

.slider-handle::before {
  content: '';
  position: absolute;
  top: 50%;
  left: 50%;
  width: 28px;
  height: 28px;
  background: white;
  border: 2px solid #4a5568;
  border-radius: 50%;
  transform: translate(-50%, -50%);
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.1);
}

.handle-icon {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  display: flex;
  align-items: center;
  gap: 1px;
  color: #4a5568;
}

/* Actions */
.actions {
  display: flex;
  gap: 1rem;
  justify-content: center;
}

.action-btn {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.75rem 1.5rem;
  border-radius: 10px;
  font-size: 0.875rem;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  border: none;
  text-decoration: none;
}

.action-btn.primary {
  background: #4a5568;
  color: white;
  box-shadow: 0 2px 8px rgba(74, 85, 104, 0.2);
}

.action-btn.primary:hover {
  background: #2d3748;
  transform: translateY(-1px);
  box-shadow: 0 4px 16px rgba(74, 85, 104, 0.25);
}

.action-btn.secondary {
  background: white;
  color: #4a5568;
  border: 1px solid #cbd5e0;
}

.action-btn.secondary:hover {
  background: #f8fafc;
  border-color: #a0aec0;
}

/* Error State */
.error-state {
  text-align: center;
  padding: 3rem 2rem;
  color: #4a5568;
  background: white;
  border-radius: 12px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.05);
  border: 1px solid #fed7d7;
}

.error-icon {
  width: 3rem;
  height: 3rem;
  margin: 0 auto 1rem;
  color: #e53e3e;
  background: #fed7d7;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
}

.error-state h3 {
  font-size: 1.125rem;
  font-weight: 600;
  margin-bottom: 0.5rem;
  color: #2d3748;
}

.error-state p {
  color: #718096;
}

/* Responsive Design */
@media (max-width: 768px) {
  .main {
    padding: 0 1rem 1rem;
  }

  .method-tabs {
    grid-template-columns: 1fr;
  }

  .control-grid {
    grid-template-columns: 1fr;
  }

  .actions {
    flex-direction: column;
  }

  .brand {
    flex-direction: column;
    gap: 0.5rem;
  }

  .brand h1 {
    font-size: 2rem;
  }
}

@media (max-width: 480px) {
  .upload-zone {
    padding: 2rem 1rem;
  }

  .results-section {
    padding: 1.5rem;
  }

  .comparison-view {
    height: 400px;
  }
}
</style>