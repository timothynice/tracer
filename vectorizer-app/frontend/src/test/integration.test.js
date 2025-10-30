import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import App from '../App.vue'
import { server } from './setup.js'
import { http, HttpResponse } from 'msw'

describe('Integration Tests', () => {
  let wrapper

  beforeEach(() => {
    wrapper = mount(App)
  })

  describe('Complete File Upload and Vectorization Flow', () => {
    it('handles complete workflow from upload to display', async () => {
      // Create a test file
      const file = new File(['test image data'], 'test.png', { type: 'image/png' })

      // Start the upload process
      await wrapper.vm.processFile(file)

      // Wait for async operations to complete
      await wrapper.vm.$nextTick()
      await new Promise(resolve => setTimeout(resolve, 50))

      // Check that the file was processed
      expect(wrapper.vm.originalFile).toBe(file)
      expect(wrapper.vm.originalImage).toBeTruthy()
      expect(wrapper.vm.results).toBeTruthy()
      expect(wrapper.vm.loading).toBe(false)

      // Check that results are displayed
      expect(wrapper.find('.results').exists()).toBe(true)
      expect(wrapper.find('.method-selector').exists()).toBe(true)
      expect(wrapper.find('.comparison-view').exists()).toBe(true)
    })

    it('handles file drop workflow', async () => {
      const file = new File(['test image data'], 'test.png', { type: 'image/png' })

      // Create mock DataTransfer
      const dataTransfer = {
        files: [file]
      }

      const uploadArea = wrapper.find('.upload-area')

      // Simulate drag over
      await uploadArea.trigger('dragover', { dataTransfer })
      expect(wrapper.vm.isDragOver).toBe(true)

      // Simulate drop
      await uploadArea.trigger('drop', { dataTransfer })

      // Wait for processing
      await wrapper.vm.$nextTick()
      await new Promise(resolve => setTimeout(resolve, 50))

      expect(wrapper.vm.isDragOver).toBe(false)
      expect(wrapper.vm.originalFile).toBe(file)
      expect(wrapper.vm.results).toBeTruthy()
    })
  })

  describe('Method Switching Integration', () => {
    beforeEach(async () => {
      // Set up component with results
      const file = new File(['test'], 'test.png', { type: 'image/png' })
      await wrapper.vm.processFile(file)
      await wrapper.vm.$nextTick()
      await new Promise(resolve => setTimeout(resolve, 50))
    })

    it('switches between vectorization methods', async () => {
      expect(wrapper.vm.selectedMethod).toBe('potrace')

      // Switch to edge detection
      const select = wrapper.find('.method-selector select')
      await select.setValue('opencv_edge')

      expect(wrapper.vm.selectedMethod).toBe('opencv_edge')

      // Check that the SVG content changes
      const svgContent = wrapper.vm.getCurrentSVG()
      expect(svgContent).toContain('stroke="black"')
      expect(svgContent).toContain('fill="none"')
    })

    it('updates display names when switching methods', async () => {
      const select = wrapper.find('.method-selector select')

      await select.setValue('opencv_contour')
      await wrapper.vm.$nextTick()

      const sliderLabel = wrapper.find('.slider-label span:last-child')
      expect(sliderLabel.text()).toContain('OpenCV (Filled Contours)')
    })
  })

  describe('Parameter Changes Integration', () => {
    beforeEach(async () => {
      const file = new File(['test'], 'test.png', { type: 'image/png' })
      await wrapper.vm.processFile(file)
      await wrapper.vm.$nextTick()
      await new Promise(resolve => setTimeout(resolve, 50))

      await wrapper.setData({ showParameters: true })
      await wrapper.vm.$nextTick()
    })

    it('triggers API calls when parameters change', async () => {
      // Mock the API to track calls
      const apiCalls = []
      server.use(
        http.post('http://localhost:8000/vectorize', ({ request }) => {
          apiCalls.push('vectorize-called')
          return HttpResponse.json({
            success: true,
            original_image: 'data:image/png;base64,test',
            vectorized: {
              potrace: '<svg>updated</svg>'
            },
            parameters_used: {}
          })
        })
      )

      // Change a parameter that triggers immediate reprocessing
      const invertCheckbox = wrapper.find('input[type="checkbox"]')
      await invertCheckbox.setChecked(true)

      // Wait for API call
      await wrapper.vm.$nextTick()
      await new Promise(resolve => setTimeout(resolve, 50))

      expect(apiCalls.length).toBeGreaterThan(0)
      expect(wrapper.vm.parameters.potrace.invert).toBe(true)
    })

    it('debounces slider parameter changes', async () => {
      vi.useFakeTimers()

      const apiCalls = []
      server.use(
        http.post('http://localhost:8000/vectorize', ({ request }) => {
          apiCalls.push('vectorize-called')
          return HttpResponse.json({
            success: true,
            original_image: 'data:image/png;base64,test',
            vectorized: { potrace: '<svg>updated</svg>' },
            parameters_used: {}
          })
        })
      )

      // Change parameters multiple times quickly
      await wrapper.setData({ selectedMethod: 'potrace' })
      await wrapper.vm.$nextTick()

      const slider = wrapper.find('input[type="range"]')
      await slider.setValue(5)
      await slider.setValue(7)
      await slider.setValue(9)

      // Fast forward through debounce period
      vi.advanceTimersByTime(600)
      await wrapper.vm.$nextTick()

      // Should only make one API call due to debouncing
      expect(apiCalls.length).toBeLessThanOrEqual(1)

      vi.useRealTimers()
    })
  })

  describe('Error Handling Integration', () => {
    it('handles API errors gracefully', async () => {
      // Mock API to return error
      server.use(
        http.post('http://localhost:8000/vectorize', () => {
          return new HttpResponse(null, {
            status: 500,
            statusText: 'Internal Server Error'
          })
        })
      )

      const file = new File(['test'], 'test.png', { type: 'image/png' })
      await wrapper.vm.processFile(file)

      // Wait for error handling
      await wrapper.vm.$nextTick()
      await new Promise(resolve => setTimeout(resolve, 50))

      expect(wrapper.vm.error).toBeTruthy()
      expect(wrapper.find('.error').exists()).toBe(true)
    })

    it('handles network connectivity issues', async () => {
      // Mock network failure
      server.use(
        http.post('http://localhost:8000/vectorize', () => {
          return HttpResponse.error()
        })
      )

      const file = new File(['test'], 'test.png', { type: 'image/png' })
      await wrapper.vm.processFile(file)

      await wrapper.vm.$nextTick()
      await new Promise(resolve => setTimeout(resolve, 50))

      expect(wrapper.vm.error).toBeTruthy()
      expect(wrapper.find('.error').exists()).toBe(true)
    })

    it('recovers from errors on new upload', async () => {
      // First upload with error
      server.use(
        http.post('http://localhost:8000/vectorize', () => {
          return new HttpResponse(null, { status: 500 })
        })
      )

      const file1 = new File(['test'], 'test1.png', { type: 'image/png' })
      await wrapper.vm.processFile(file1)
      await wrapper.vm.$nextTick()
      await new Promise(resolve => setTimeout(resolve, 50))

      expect(wrapper.vm.error).toBeTruthy()

      // Second upload with success
      server.use(
        http.post('http://localhost:8000/vectorize', () => {
          return HttpResponse.json({
            success: true,
            original_image: 'data:image/png;base64,test',
            vectorized: { potrace: '<svg>success</svg>' },
            parameters_used: {}
          })
        })
      )

      const file2 = new File(['test'], 'test2.png', { type: 'image/png' })
      await wrapper.vm.processFile(file2)
      await wrapper.vm.$nextTick()
      await new Promise(resolve => setTimeout(resolve, 50))

      expect(wrapper.vm.error).toBe(null)
      expect(wrapper.find('.error').exists()).toBe(false)
      expect(wrapper.find('.results').exists()).toBe(true)
    })
  })

  describe('Performance and User Experience', () => {
    it('shows loading states during processing', async () => {
      let resolvePromise
      const slowPromise = new Promise(resolve => {
        resolvePromise = resolve
      })

      // Mock slow API response
      server.use(
        http.post('http://localhost:8000/vectorize', async () => {
          await slowPromise
          return HttpResponse.json({
            success: true,
            original_image: 'data:image/png;base64,test',
            vectorized: { potrace: '<svg>test</svg>' },
            parameters_used: {}
          })
        })
      )

      const file = new File(['test'], 'test.png', { type: 'image/png' })
      const processPromise = wrapper.vm.processFile(file)

      // Check loading state is shown
      await wrapper.vm.$nextTick()
      expect(wrapper.vm.loading).toBe(true)
      expect(wrapper.find('.loading').exists()).toBe(true)
      expect(wrapper.find('.spinner').exists()).toBe(true)

      // Resolve the slow API call
      resolvePromise()
      await processPromise
      await wrapper.vm.$nextTick()

      // Loading should be cleared
      expect(wrapper.vm.loading).toBe(false)
      expect(wrapper.find('.loading').exists()).toBe(false)
    })

    it('handles large file uploads', async () => {
      // Create a larger mock file
      const largeFileData = new Array(10000).fill('x').join('')
      const file = new File([largeFileData], 'large.png', { type: 'image/png' })

      const startTime = Date.now()
      await wrapper.vm.processFile(file)
      await wrapper.vm.$nextTick()
      await new Promise(resolve => setTimeout(resolve, 50))
      const endTime = Date.now()

      // Should complete in reasonable time
      expect(endTime - startTime).toBeLessThan(1000)
      expect(wrapper.vm.results).toBeTruthy()
    })
  })

  describe('UI State Consistency', () => {
    it('maintains UI consistency during method switches', async () => {
      const file = new File(['test'], 'test.png', { type: 'image/png' })
      await wrapper.vm.processFile(file)
      await wrapper.vm.$nextTick()
      await new Promise(resolve => setTimeout(resolve, 50))

      const initialSliderValue = wrapper.vm.sliderValue

      // Switch method
      const select = wrapper.find('.method-selector select')
      await select.setValue('opencv_edge')
      await wrapper.vm.$nextTick()

      // Slider position should remain the same
      expect(wrapper.vm.sliderValue).toBe(initialSliderValue)

      // Comparison view should still be functional
      expect(wrapper.find('.comparison-view').exists()).toBe(true)
      expect(wrapper.find('.slider-line').exists()).toBe(true)
    })

    it('synchronizes parameter panel with selected method', async () => {
      const file = new File(['test'], 'test.png', { type: 'image/png' })
      await wrapper.vm.processFile(file)
      await wrapper.vm.$nextTick()
      await new Promise(resolve => setTimeout(resolve, 50))

      await wrapper.setData({ showParameters: true })
      await wrapper.vm.$nextTick()

      // Initially shows potrace parameters
      expect(wrapper.find('h4').text()).toBe('Potrace Settings')

      // Switch to edge detection
      await wrapper.find('.method-selector select').setValue('opencv_edge')
      await wrapper.vm.$nextTick()

      // Should now show edge detection parameters
      expect(wrapper.find('h4').text()).toBe('OpenCV Edge Detection Settings')
    })
  })

  describe('Download Integration', () => {
    beforeEach(async () => {
      const file = new File(['test'], 'test.png', { type: 'image/png' })
      await wrapper.vm.processFile(file)
      await wrapper.vm.$nextTick()
      await new Promise(resolve => setTimeout(resolve, 50))

      // Mock DOM methods for download
      const mockLink = {
        href: '',
        download: '',
        click: vi.fn(),
        remove: vi.fn()
      }
      vi.spyOn(document, 'createElement').mockReturnValue(mockLink)
      vi.spyOn(document.body, 'appendChild').mockImplementation(() => {})
      vi.spyOn(document.body, 'removeChild').mockImplementation(() => {})
    })

    it('downloads correct file for selected method', async () => {
      // Switch to edge detection
      await wrapper.find('.method-selector select').setValue('opencv_edge')
      await wrapper.vm.$nextTick()

      // Click download button
      await wrapper.find('.download-btn').trigger('click')

      expect(document.createElement).toHaveBeenCalledWith('a')
      expect(document.body.appendChild).toHaveBeenCalled()

      // Should download the correct method's result
      const expectedContent = wrapper.vm.getCurrentSVG()
      expect(expectedContent).toContain('stroke="black"')
    })
  })

  describe('Accessibility and User Interactions', () => {
    it('maintains keyboard accessibility', async () => {
      const file = new File(['test'], 'test.png', { type: 'image/png' })
      await wrapper.vm.processFile(file)
      await wrapper.vm.$nextTick()
      await new Promise(resolve => setTimeout(resolve, 50))

      // All interactive elements should be keyboard accessible
      const select = wrapper.find('.method-selector select')
      const toggleBtn = wrapper.find('.toggle-params-btn')
      const slider = wrapper.find('.comparison-slider')

      expect(select.element.tagName).toBe('SELECT') // Inherently keyboard accessible
      expect(toggleBtn.element.tagName).toBe('BUTTON') // Inherently keyboard accessible
      expect(slider.element.type).toBe('range') // Inherently keyboard accessible
    })

    it('provides appropriate feedback during interactions', async () => {
      const file = new File(['test'], 'test.png', { type: 'image/png' })
      await wrapper.vm.processFile(file)
      await wrapper.vm.$nextTick()
      await new Promise(resolve => setTimeout(resolve, 50))

      await wrapper.setData({ showParameters: true })
      await wrapper.vm.$nextTick()

      // Parameter loading feedback
      wrapper.vm.debouncedReprocess()
      expect(wrapper.vm.parameterLoading).toBe(true)

      // Visual feedback should be present
      const svgContainer = wrapper.find('.svg-container')
      expect(svgContainer.classes()).toContain('loading-svg')
    })
  })
})