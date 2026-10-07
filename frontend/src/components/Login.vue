<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { ArrowRight, Eye, EyeOff, Leaf, LoaderCircle, RefreshCw, ShieldCheck } from 'lucide-vue-next'
import { fetchCaptcha, useAuth } from '../lib/auth'

const { login, sessionError } = useAuth()
const username = ref('')
const password = ref('')
const code = ref('')
const visiblePassword = ref(false)
const captcha = ref<{ captcha_id: string; image: string; expires_in: number } | null>(null)
const captchaLoading = ref(false)
const captchaError = ref('')
const error = ref('')
const submitting = ref(false)
const usernameInput = ref<HTMLInputElement>()
let controller: AbortController | undefined

async function reloadCaptcha() {
  controller?.abort()
  const own = new AbortController(); controller = own
  captchaLoading.value = true; captchaError.value = ''; captcha.value = null; code.value = ''
  try { const result = await fetchCaptcha(own.signal); if (!own.signal.aborted) captcha.value = result }
  catch (e) { if (!own.signal.aborted) captchaError.value = e instanceof Error ? e.message : '验证码加载失败' }
  finally { if (!own.signal.aborted) captchaLoading.value = false }
}

async function submit() {
  if (submitting.value || !captcha.value) return
  submitting.value = true; error.value = ''
  try {
    await login({ username: username.value.trim(), password: password.value, captcha_id: captcha.value.captcha_id, captcha_code: code.value.trim() })
    password.value = ''
  } catch (e) {
    error.value = e instanceof Error ? e.message : '登录失败，请重试'
    password.value = ''; await reloadCaptcha()
  } finally { submitting.value = false }
}
onMounted(() => { usernameInput.value?.focus(); void reloadCaptcha() })
onBeforeUnmount(() => { controller?.abort(); password.value = '' })
</script>

<template>
  <main class="login-page">
    <section class="login-story" aria-label="SalesMind 销售分析">
      <a class="login-brand" href="/" aria-label="SalesMind 首页"><span><Leaf :size="24" /></span>SalesMind.</a>
      <div class="login-story-content"><span class="eyebrow">CLARITY STARTS HERE.</span><h1>每一个好问题，<br />都有数据作答。</h1><p>连接你的销售数据，<br />让每一次决策都有据可循。</p><div class="login-art" aria-hidden="true"><div class="art-caption">A CLEARER PICTURE</div><div class="art-bars"><i></i><i></i><i></i><i></i><i></i><i></i><i></i></div><div class="art-axis"><span>发现趋势</span><span>看见增长 ↗</span></div></div></div>
      <div class="login-story-footer">少一点报表，多一点洞察。</div>
    </section>
    <section class="login-form-side" aria-labelledby="login-heading">
      <div class="login-form-wrap"><span class="login-welcome">WELCOME BACK</span><h2 id="login-heading">欢迎回来。</h2><p class="login-intro">登录你的销售分析空间。</p>
        <form @submit.prevent="submit">
          <div v-if="error || sessionError" class="login-error" role="alert">{{ error || sessionError }}</div>
          <label class="login-label" for="username">用户名</label><input id="username" ref="usernameInput" v-model="username" class="login-input" name="username" autocomplete="username" placeholder="请输入用户名" required maxlength="64" :disabled="submitting" />
          <label class="login-label" for="password">密码</label><div class="password-input"><input id="password" v-model="password" class="login-input" name="password" :type="visiblePassword ? 'text' : 'password'" autocomplete="current-password" placeholder="请输入密码" required maxlength="128" :disabled="submitting" /><button type="button" :aria-label="visiblePassword ? '隐藏密码' : '显示密码'" @click="visiblePassword = !visiblePassword"><EyeOff v-if="visiblePassword" :size="18" /><Eye v-else :size="18" /></button></div>
          <label class="login-label" for="captcha-code">图片验证码</label><div class="captcha-row"><input id="captcha-code" v-model="code" class="login-input" name="captcha" placeholder="输入 4 位验证码" autocomplete="off" required minlength="4" maxlength="4" :disabled="submitting" /><button class="captcha-image" type="button" aria-label="更换验证码" :disabled="submitting || captchaLoading" @click="reloadCaptcha"><img v-if="captcha" :src="captcha.image" alt="登录图片验证码" /><LoaderCircle v-else-if="captchaLoading" class="spin" :size="20" /><span v-else>重新获取</span></button></div><div class="captcha-hint"><span>忽略大小写 · 2 分钟内有效</span><button type="button" :disabled="submitting || captchaLoading" @click="reloadCaptcha"><RefreshCw :size="12" />换一张</button></div><p v-if="captchaError" class="captcha-error" role="alert">{{ captchaError }}</p>
          <button class="login-submit" type="submit" :disabled="submitting || !captcha || !username.trim() || !password || code.trim().length !== 4"><LoaderCircle v-if="submitting" class="spin" :size="17" /><template v-else>登录分析空间<ArrowRight :size="17" /></template></button>
        </form>
        <div class="login-security"><ShieldCheck :size="15" /><span>仅向已登录账号开放销售数据</span></div><p class="login-admin-note">需要账号或忘记密码？请联系管理员。</p>
      </div><footer class="login-footer">SalesMind · 让数据，开口说话。</footer>
    </section>
  </main>
</template>
