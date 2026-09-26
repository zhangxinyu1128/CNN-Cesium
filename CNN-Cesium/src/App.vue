<script setup lang="ts">
import { onMounted, ref } from 'vue'
import WorkspaceApp from './App.workspace.vue'

const showWorkspace = ref(new URLSearchParams(window.location.search).get('workspace') === '1')

onMounted(() => {
  if (!showWorkspace.value) {
    window.location.replace('/legacy/index.html')
  }
})
</script>

<template>
  <WorkspaceApp v-if="showWorkspace" />
  <main v-else class="redirecting">
    正在打开历年台风展示监控平台...
  </main>
</template>

<style scoped>
.redirecting {
  display: grid;
  width: 100vw;
  height: 100vh;
  place-items: center;
  color: #d9ffff;
  background: #06121c;
  font: 16px/1.5 'Microsoft YaHei', sans-serif;
}
</style>

