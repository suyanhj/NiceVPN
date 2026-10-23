<template>
  <div
    class="vpn-v2-spotlight"
    :class="{ 'is-active': active }"
    :style="{ '--spot-x': x + '%', '--spot-y': y + '%' }"
    @mousemove="move"
    @mouseenter="enter"
    @mouseleave="leave"
    aria-hidden="true"
  >
    <div class="vpn-v2-spotlight__glow"></div>
    <div class="vpn-v2-spotlight__beam"></div>
  </div>
</template>

<script>
export default {
  data() {
    return {
      x: 50,
      y: 45,
      active: false,
    };
  },
  methods: {
    move(event) {
      const rect = event.currentTarget.getBoundingClientRect();
      this.x = ((event.clientX - rect.left) / rect.width) * 100;
      this.y = ((event.clientY - rect.top) / rect.height) * 100;
    },
    enter(event) {
      this.active = true;
      this.move(event);
    },
    leave() {
      this.active = false;
    },
  },
};
</script>

<style scoped>
.vpn-v2-spotlight {
  position: absolute;
  inset: 0;
  z-index: 0;
  overflow: hidden;
  border-radius: inherit;
  pointer-events: auto;
}

.vpn-v2-spotlight__glow {
  position: absolute;
  inset: -1px;
  opacity: 0;
  background:
    radial-gradient(
      260px circle at var(--spot-x) var(--spot-y),
      rgba(88, 166, 255, 0.16),
      rgba(68, 215, 196, 0.07) 34%,
      transparent 66%
    );
  transition: opacity 220ms ease;
}

.vpn-v2-spotlight__beam {
  position: absolute;
  top: -45%;
  right: -10%;
  width: 55%;
  height: 150%;
  opacity: 0.24;
  transform: rotate(18deg);
  background: linear-gradient(
    180deg,
    transparent,
    rgba(110, 168, 255, 0.08) 42%,
    rgba(91, 224, 204, 0.035) 58%,
    transparent
  );
  filter: blur(12px);
}

.vpn-v2-spotlight.is-active .vpn-v2-spotlight__glow {
  opacity: 1;
}

@media (prefers-reduced-motion: reduce) {
  .vpn-v2-spotlight__glow {
    transition: none;
  }
}
</style>
