<script setup>
import Sidebar from './Sidebar.vue'
import AppHeader from './AppHeader.vue'
import BottomNav from './BottomNav.vue'
import ToastContainer from '../common/ToastContainer.vue'
</script>

<template>
  <div class="flex h-screen overflow-hidden">
    <Sidebar />
    <div class="flex flex-1 flex-col overflow-hidden">
      <AppHeader />
      <main class="flex-1 overflow-y-auto bg-gray-50 p-6 pb-20 lg:pb-6">
        <router-view />
      </main>
    </div>
    <BottomNav />
    <ToastContainer />
  </div>
</template>
