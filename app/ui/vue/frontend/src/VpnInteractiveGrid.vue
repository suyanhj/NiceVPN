<script setup lang="ts">
/**
 * Adapted from Inspira UI InteractiveGridPattern (MIT).
 * NiceVPN keeps it presentation-only; no VPN data is read here.
 */
import { computed, ref } from "vue";
import "./styles.css";

interface Props {
  width?: number;
  height?: number;
  columns?: number;
  rows?: number;
}

const props = withDefaults(defineProps<Props>(), {
  width: 36,
  height: 36,
  columns: 24,
  rows: 18,
});

const hovered = ref<number | null>(null);
const total = computed(() => props.columns * props.rows);
const gridWidth = computed(() => props.width * props.columns);
const gridHeight = computed(() => props.height * props.rows);

function x(index: number) {
  return (index % props.columns) * props.width;
}

function y(index: number) {
  return Math.floor(index / props.columns) * props.height;
}
</script>

<template>
  <svg
    :width="gridWidth"
    :height="gridHeight"
    class="pointer-events-auto absolute inset-0 h-full w-full opacity-45"
    aria-hidden="true"
  >
    <rect
      v-for="(_, index) in total"
      :key="index"
      :x="x(index)"
      :y="y(index)"
      :width="width"
      :height="height"
      class="fill-transparent stroke-white/[0.035] transition-colors duration-700"
      :class="{ '!fill-[#6ea8ff]/[0.055] !stroke-[#6ea8ff]/10 !duration-100': hovered === index }"
      @mouseenter="hovered = index"
      @mouseleave="hovered = null"
    />
  </svg>
</template>
