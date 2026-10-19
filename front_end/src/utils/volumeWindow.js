/** Completed one-second delta-volume windows; first observations are baselines. */
export class VolumeWindow {
  constructor() { this.last = null; this.history = []; this.volume = 0; this.complete = false }

  observe(cumulative, time, day) {
    if (!Number.isFinite(cumulative) || cumulative < 0 || !Number.isFinite(time)) return null
    const bucket = Math.floor(time / 1000)
    const previous = this.last
    if (previous && time <= previous.time) return null
    if (!previous || day !== previous.day || cumulative < previous.cumulative || time - previous.time > 2000 || bucket - previous.bucket > 1) {
      this.history = []; this.volume = 0
      this.complete = false
      this.last = { cumulative, time, day, bucket }
      return null
    }
    let result = null
    if (bucket !== previous.bucket) {
      const average = this.history.length ? this.history.reduce((a, b) => a + b, 0) / this.history.length : 0
      if (this.history.length >= 5 && average > 0) result = { volume: this.volume, average, ratio: this.volume / average }
      if (this.complete) this.history.push(this.volume)
      this.complete = true
      if (this.history.length > 50) this.history.shift()
      this.volume = 0
    }
    this.volume += cumulative - previous.cumulative
    this.last = { cumulative, time, day, bucket }
    return result
  }
}
