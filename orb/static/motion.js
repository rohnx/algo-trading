// Transient chart geometry only; quote and strategy data stay authoritative.
class ChartMotion {
  constructor() {
    this.duration = 180;
    this.tracks = new Map();
    this.seen = new Set();
    this.reset();
  }

  reset() {
    this.identity = null;
    this.frameTime = 0;
    this.enabled = true;
    this.active = false;
    this.domain = null;
    this.tracks.clear();
    this.seen.clear();
  }

  begin(identity, now, enabled = true) {
    if (identity !== this.identity) {
      this.reset();
      this.identity = identity;
    }
    this.frameTime = now;
    this.enabled = enabled;
    this.active = false;
    this.seen.clear();
    if (!enabled) this.finish();
  }

  sample(track) {
    const progress = Math.max(0, Math.min(1, (this.frameTime - track.started) / this.duration));
    if (!this.enabled || progress >= 1 || track.from === track.to) return track.to;
    return track.from + (track.to - track.from) * (1 - (1 - progress) ** 3);
  }

  value(key, target, initial = target) {
    this.seen.add(key);
    if (!Number.isFinite(target)) {
      this.tracks.delete(key);
      return target;
    }
    let track = this.tracks.get(key);
    if (!track) {
      track = { from: Number.isFinite(initial) ? initial : target, to: target, started: this.frameTime };
      this.tracks.set(key, track);
    } else if (track.to !== target) {
      track.from = this.sample(track);
      track.to = target;
      track.started = this.frameTime;
    }
    if (!this.enabled) track.from = track.to;
    const shown = this.sample(track);
    if (shown !== track.to) this.active = true;
    return shown;
  }

  finish() {
    for (const track of this.tracks.values()) {
      track.from = track.to;
      track.started = this.frameTime;
    }
    this.active = false;
  }

  end() {
    this.active = false;
    for (const [key, track] of this.tracks) {
      if (!this.seen.has(key)) this.tracks.delete(key);
      else if (this.enabled && this.sample(track) !== track.to) this.active = true;
    }
    return this.active;
  }

  fit(min, max, minimumSpan = 20) {
    const span = Math.max(max - min, minimumSpan);
    const center = (min + max) / 2;
    const fitted = { min: center - span * .62, max: center + span * .62 };
    if (!this.domain) {
      this.domain = { ...fitted, shownMin: fitted.min, shownMax: fitted.max };
    } else {
      const edge = (this.domain.max - this.domain.min) * .04;
      if (min <= this.domain.min + edge || max >= this.domain.max - edge) {
        this.domain.min = Math.min(this.domain.min, fitted.min);
        this.domain.max = Math.max(this.domain.max, fitted.max);
      }
    }
    const margin = span * .015;
    this.domain.shownMin = Math.min(this.domain.shownMin, this.value('__domain:min', this.domain.min), min - margin);
    this.domain.shownMax = Math.max(this.domain.shownMax, this.value('__domain:max', this.domain.max), max + margin);
    return { min: this.domain.shownMin, max: this.domain.shownMax };
  }
}

globalThis.ChartMotion = ChartMotion;
