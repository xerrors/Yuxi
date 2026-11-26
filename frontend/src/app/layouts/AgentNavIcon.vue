<script setup>
defineProps({
  size: { type: [Number, String], default: 18 }
})
</script>

<template>
  <svg
    :width="size"
    :height="size"
    viewBox="1.5 1.5 21 21"
    fill="none"
    aria-hidden="true"
    focusable="false"
  >
    <rect x="2.8" y="4.7" width="18.4" height="14.6" rx="5.8" stroke="currentColor" stroke-width="1.7" />
    <g class="agent-nav-eyes" fill="currentColor">
      <rect x="8" y="10" width="2.1" height="4" rx="1.05" />
      <rect x="13.9" y="10" width="2.1" height="4" rx="1.05" />
    </g>
  </svg>
</template>

<style scoped>
@media (prefers-reduced-motion: no-preference) {
  .agent-nav-eyes {
    transform-box: fill-box;
    transform-origin: center;
    animation: agent-nav-blink 5s ease-in-out infinite;
  }
}

@keyframes agent-nav-blink {
  0%,
  69%,
  73.5%,
  100% {
    transform: scaleY(1);
  }

  70.8%,
  71.5% {
    transform: scaleY(0.22);
  }
}
</style>
