<script setup lang="ts">
import { onMounted } from 'vue'
import { Leaf, LoaderCircle } from 'lucide-vue-next'
import Workbench from './Workbench.vue'
import Login from './components/Login.vue'
import { useAuth } from './lib/auth'

const { user, initializing, initializeAuth } = useAuth()
onMounted(initializeAuth)
</script>

<template>
  <div v-if="initializing" class="auth-loading" role="status"><Leaf :size="34" /><h2>SalesMind.</h2><p><LoaderCircle :size="16" class="spin" />正在恢复登录…</p></div>
  <Workbench v-else-if="user" :key="user.id" />
  <Login v-else />
</template>
