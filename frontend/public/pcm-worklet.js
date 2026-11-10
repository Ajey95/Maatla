class PCMWorklet extends AudioWorkletProcessor {
  constructor() {
    super()
    this.samples = []
    this.position = 0
    this.ratio = sampleRate / 16000
  }
  process(inputs, outputs) {
    const input = inputs[0] && inputs[0][0]
    if (input) {
      while (this.position < input.length) {
        const i = Math.floor(this.position)
        const sample = input[i] || 0
        this.samples.push(Math.max(-1, Math.min(1, sample)))
        this.position += this.ratio
        if (this.samples.length === 512) {
          const frame = new ArrayBuffer(1024)
          const view = new DataView(frame)
          for (let j = 0; j < 512; j++) {
            const value = this.samples[j]
            view.setInt16(j * 2, value < 0 ? value * 32768 : value * 32767, true)
          }
          this.port.postMessage(frame, [frame])
          this.samples = []
        }
      }
      this.position -= input.length
    }
    for (const channel of outputs[0] || []) channel.fill(0)
    return true
  }
}
registerProcessor('maatla-pcm', PCMWorklet)
