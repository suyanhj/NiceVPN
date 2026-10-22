<script setup lang="ts">
import { computed, ref } from "vue";
import { motion } from "motion-v";
import "./styles.css";

const x = ref(50);
const y = ref(45);
const active = ref(false);

const spotlightStyle = computed(() => ({
  background: `radial-gradient(260px circle at ${x.value}% ${y.value}%, rgba(88, 166, 255, 0.16), rgba(68, 215, 196, 0.07) 34%, transparent 66%)`,
}));

function move(event: MouseEvent) {
  const current = event.currentTarget;
  if (!(current instanceof HTMLElement)) return;
  const rect = current.getBoundingClientRect();
  x.value = ((event.clientX - rect.left) / rect.width) * 100;
  y.value = ((event.clientY - rect.top) / rect.height) * 100;
}

function enter(event: MouseEvent) {
  active.value = true;
  move(event);
}

function leave() {
  active.value = false;
}
</script>

<template>
  <div
    class="vpn-motion-safe absolute inset-0 overflow-hidden rounded-[inherit]"
    aria-hidden="true"
    @mousemove="move"
    @mouseenter="enter"
    @mouseleave="leave"
  >
    <motion.div
      class="pointer-events-none absolute -inset-px"
      :style="spotlightStyle"
      :animate="{ opacity: active ? 1 : 0 }"
      :transition="{ duration: 0.2, ease: 'easeOut' }"
    />
    <motion.div
      class="pointer-events-none absolute -top-[45%] -right-[10%] h-[150%] w-[55%] rotate-[18deg] bg-gradient-to-b from-transparent via-[rgba(110,168,255,0.08)] to-transparent blur-xl"
      :animate="{ opacity: active ? 0.34 : 0.2, x: active ? -4 : 0 }"
      :transition="{ type: 'spring', stiffness: 140, damping: 24 }"
    />
  </div>
</template>
