<template>
  <div class="login-view" :class="{ 'has-alert': serverStatus === 'error' }">
    <!-- 服务状态提示 -->
    <div v-if="serverStatus === 'error'" class="server-status-alert">
      <div class="alert-content">
        <exclamation-circle-icon class="alert-icon" size="20" />
        <div class="alert-text">
          <div class="alert-title">服务端连接失败</div>
          <div class="alert-message">{{ serverError }}</div>
        </div>
        <a-button type="link" size="small" @click="checkServerHealth" :loading="healthChecking">
          重试
        </a-button>
      </div>
    </div>

    <!-- 顶部导航：品牌名称 & 操作按钮 -->
    <nav class="login-navbar">
      <div class="navbar-content">
        <div class="brand-container" @click="goHome" style="cursor: pointer">
          <img v-if="brandLogo" :src="brandLogo" alt="logo" class="brand-logo" />
          <h1 class="brand-text">
            <span class="brand-main">{{ brandName }}</span>
          </h1>
        </div>
      </div>
    </nav>

    <!-- 主要内容区：居中卡片 -->
    <main class="login-main">
      <div class="login-card">
        <!-- 左侧图片 -->
        <div class="card-side is-image">
          <img
            v-if="customLoginBg"
            :src="customLoginBg"
            alt="登录背景"
            class="login-bg-image"
            @error="handleLoginBgError"
          />
          <!-- 同一张植物插画通过局部色板适配主题，装饰不参与交互。 -->
          <svg
            v-else
            class="login-bg-art"
            viewBox="0 0 640 760"
            preserveAspectRatio="xMidYMid slice"
            aria-hidden="true"
            focusable="false"
          >
            <!-- 绘制属性随图元克隆，不依赖 <use> 实例树之外的祖先选择器。 -->
            <defs>
              <linearGradient id="login-paper" x2="0.8" y2="1">
                <stop stop-color="var(--paper-light)" />
                <stop offset="1" stop-color="var(--paper)" />
              </linearGradient>
              <radialGradient id="login-wash">
                <stop stop-color="var(--wash)" stop-opacity="0.24" />
                <stop offset="1" stop-color="var(--wash)" stop-opacity="0" />
              </radialGradient>
              <linearGradient id="login-wood" x1="0" y1="1" x2="1" y2="0">
                <stop stop-color="var(--wood)" />
                <stop offset="1" stop-color="var(--wood-light)" />
              </linearGradient>
              <linearGradient id="login-leaf" x1="0" y1="0.8" x2="0.9" y2="0.2">
                <stop stop-color="var(--leaf-deep)" />
                <stop offset="0.48" stop-color="var(--leaf)" />
                <stop offset="1" stop-color="var(--leaf-light)" />
              </linearGradient>
              <radialGradient id="login-petal" cx="0.46" cy="0.94" r="0.92">
                <stop stop-color="var(--petal-deep)" />
                <stop offset="0.45" stop-color="var(--petal-mid)" />
                <stop offset="1" stop-color="var(--petal)" />
              </radialGradient>
              <radialGradient id="login-heart">
                <stop stop-color="var(--heart)" />
                <stop offset="1" stop-color="var(--petal-deep)" stop-opacity="0" />
              </radialGradient>
              <filter id="login-paper-grain" x="0" y="0" width="100%" height="100%">
                <feTurbulence
                  type="fractalNoise"
                  baseFrequency="0.72"
                  numOctaves="3"
                  stitchTiles="stitch"
                />
                <feColorMatrix type="saturate" values="0" />
              </filter>
              <g id="login-leaf-shape">
                <path
                  fill="url(#login-leaf)"
                  stroke="var(--leaf-deep)"
                  stroke-width="0.65"
                  stroke-opacity="0.35"
                  d="M0 0 C-15-18-35-57-17-101 C8-86 30-35 0 0Z"
                />
                <path
                  fill="var(--leaf-deep)"
                  opacity="0.12"
                  d="M0 0 C-4-32-7-68-17-101 C8-86 30-35 0 0Z"
                />
                <path
                  fill="none"
                  stroke="var(--leaf-vein)"
                  stroke-width="0.8"
                  stroke-linecap="round"
                  opacity="0.7"
                  d="M0-3 C-5-33-7-66-16-96"
                />
                <path
                  fill="none"
                  stroke="var(--leaf-vein)"
                  stroke-width="0.6"
                  stroke-linecap="round"
                  opacity="0.38"
                  d="M-3-20 Q-13-24-18-35 M-5-36 Q-18-42-22-53 M-7-52 Q-20-59-22-69 M-10-69 Q-20-75-20-83 M-3-24 Q8-34 10-43 M-5-43 Q6-51 6-61 M-8-60 Q0-68-3-77"
                />
              </g>
              <g id="login-petal-shape">
                <path
                  fill="url(#login-petal)"
                  stroke="var(--petal-line)"
                  stroke-width="0.65"
                  stroke-opacity="0.35"
                  d="M0 9 C-20-3-49-23-49-51 C-49-72-39-88-22-87 C-13-96-1-93 6-88 C24-94 44-79 43-60 C49-35 26-9 0 9Z"
                />
                <path
                  fill="none"
                  stroke="var(--petal-line)"
                  stroke-width="0.65"
                  stroke-linecap="round"
                  opacity="0.18"
                  d="M-3 1 C-13-21-29-39-30-66 M1-2 C-1-29 7-48 4-74 M5 0 C20-22 30-36 30-54"
                />
                <path
                  fill="none"
                  stroke="var(--petal-rim)"
                  stroke-width="1.2"
                  stroke-linecap="round"
                  opacity="0.65"
                  d="M-45-62 C-42-79-33-85-22-82 C-12-91-3-88 5-83"
                />
              </g>
              <g id="login-stamen">
                <path
                  fill="none"
                  stroke="var(--stamen)"
                  stroke-width="0.85"
                  stroke-linecap="round"
                  d="M0 3 Q-5-11-2-26"
                />
                <ellipse
                  fill="var(--pollen)"
                  stroke="var(--stamen)"
                  stroke-width="0.4"
                  cx="-2"
                  cy="-26"
                  rx="2"
                  ry="1.3"
                  transform="rotate(-25 -2 -26)"
                />
              </g>
              <g id="login-blossom">
                <g opacity="0.93">
                  <use href="#login-petal-shape" transform="rotate(4) scale(1 1.03)" />
                  <use href="#login-petal-shape" transform="rotate(54) scale(0.94 1)" />
                  <use href="#login-petal-shape" transform="rotate(105) scale(1.04 0.95)" />
                  <use href="#login-petal-shape" transform="rotate(157) scale(0.98 1.06)" />
                  <use href="#login-petal-shape" transform="rotate(208) scale(1.02 0.96)" />
                  <use href="#login-petal-shape" transform="rotate(261) scale(0.96 1.03)" />
                  <use href="#login-petal-shape" transform="rotate(311) scale(1 0.96)" />
                </g>
                <g transform="rotate(18) scale(0.68)">
                  <use href="#login-petal-shape" />
                  <use href="#login-petal-shape" transform="rotate(65) scale(0.94 1.04)" />
                  <use href="#login-petal-shape" transform="rotate(127)" />
                  <use href="#login-petal-shape" transform="rotate(190) scale(1.06 0.96)" />
                  <use href="#login-petal-shape" transform="rotate(250)" />
                  <use href="#login-petal-shape" transform="rotate(308) scale(0.94 1.06)" />
                </g>
                <circle r="27" fill="url(#login-heart)" />
                <g transform="translate(0 2)">
                  <use
                    v-for="n in 22"
                    :key="n"
                    href="#login-stamen"
                    :transform="`rotate(${n * 16.36}) scale(${0.78 + (n % 3) * 0.12})`"
                  />
                </g>
                <g fill="var(--pollen)">
                  <circle cx="-4" cy="-2" r="2" />
                  <circle cx="3" cy="-4" r="2.4" />
                  <circle cx="5" cy="3" r="1.8" />
                  <circle cx="-1" cy="5" r="2.2" />
                  <circle cx="-6" cy="4" r="1.4" />
                </g>
              </g>
              <g id="login-bud">
                <path
                  fill="url(#login-petal)"
                  stroke="var(--petal-line)"
                  stroke-width="0.65"
                  stroke-opacity="0.35"
                  d="M0 4 C-26-11-21-37-7-48 C12-43 25-13 0 4Z"
                />
                <path
                  fill="none"
                  stroke="var(--petal-line)"
                  stroke-width="0.65"
                  stroke-linecap="round"
                  opacity="0.18"
                  d="M0 1 Q-12-24-7-44 M2 0 Q10-20 1-34"
                />
                <path
                  fill="url(#login-leaf)"
                  stroke="var(--leaf-deep)"
                  stroke-width="0.65"
                  stroke-opacity="0.35"
                  d="M0 8 C-12 4-18-5-17-17 Q-5-11 0 0 Q8-12 13-15 C14-3 7 6 0 8Z"
                />
              </g>
            </defs>

            <rect width="640" height="760" fill="url(#login-paper)" />
            <ellipse class="wash" cx="180" cy="560" rx="350" ry="420" fill="url(#login-wash)" />
            <ellipse
              class="wash wash--late"
              cx="490"
              cy="180"
              rx="220"
              ry="260"
              fill="url(#login-wash)"
              opacity="0.5"
            />

            <!-- 淡墨远枝与前景分开，留白处只保留很轻的层次。 -->
            <g class="distant-sprig">
              <path class="twig" d="M590 795 C519 641 506 545 553 430 M527 600 Q580 567 601 507" />
              <use href="#login-leaf-shape" transform="translate(532 649) rotate(-42) scale(1.1)" />
              <use href="#login-leaf-shape" transform="translate(521 575) rotate(37) scale(0.92)" />
              <use
                href="#login-leaf-shape"
                transform="translate(532 497) rotate(-23) scale(0.78)"
              />
              <use href="#login-leaf-shape" transform="translate(565 566) rotate(60) scale(0.8)" />
            </g>

            <g class="bough">
              <path
                class="wood"
                d="M150 796 C192 678 238 562 295 445 C331 374 363 278 418 210 L421 212 C367 287 339 378 302 449 C249 568 203 682 165 799Z"
              />
              <path
                class="wood"
                d="M300 448 C311 365 278 292 255 201 L258 200 C283 279 319 360 305 438Z M271 513 C369 518 440 457 483 366 L486 367 C449 461 375 524 268 520Z M241 590 C228 563 213 547 188 551 L188 548 C218 541 235 559 246 579Z"
              />
              <path
                class="twig"
                d="M358 315 C338 265 326 208 342 152 M385 267 Q422 286 450 274 M280 293 Q246 277 228 249"
              />
              <path
                class="wood-grain"
                d="M159 780 C201 659 248 545 298 449 M302 430 Q316 370 291 298 M302 517 Q414 502 462 415"
              />

              <g opacity="0.72">
                <use
                  href="#login-leaf-shape"
                  transform="translate(181 718) rotate(-20) scale(0.85)"
                />
                <use
                  href="#login-leaf-shape"
                  transform="translate(280 500) rotate(29) scale(0.85)"
                />
                <use
                  href="#login-leaf-shape"
                  transform="translate(292 338) rotate(-65) scale(0.9)"
                />
                <use
                  href="#login-leaf-shape"
                  transform="translate(351 297) rotate(-10) scale(0.72)"
                />
                <use
                  href="#login-leaf-shape"
                  transform="translate(422 449) rotate(-23) scale(0.72)"
                />
              </g>
              <use href="#login-leaf-shape" transform="translate(218 642) rotate(23) scale(1.12)" />
              <use href="#login-leaf-shape" transform="translate(242 590) rotate(-53) scale(0.8)" />
              <use
                href="#login-leaf-shape"
                transform="translate(306 440) rotate(-60) scale(0.98)"
              />
              <use href="#login-leaf-shape" transform="translate(370 474) rotate(58) scale(0.95)" />
              <use href="#login-leaf-shape" transform="translate(280 294) rotate(40) scale(0.6)" />
              <use
                href="#login-leaf-shape"
                transform="translate(228 249) rotate(-24) scale(0.34)"
              />
              <use href="#login-leaf-shape" transform="translate(377 268) rotate(67) scale(0.72)" />
              <use href="#login-leaf-shape" transform="translate(450 274) rotate(72) scale(0.3)" />
              <use
                href="#login-leaf-shape"
                transform="translate(405 229) rotate(-36) scale(0.64)"
              />
              <use href="#login-leaf-shape" transform="translate(477 388) rotate(56) scale(0.44)" />

              <use href="#login-bud" transform="translate(257 201) rotate(-18) scale(0.57)" />
              <use href="#login-bud" transform="translate(342 152) rotate(14) scale(0.36)" />
              <use href="#login-bud" transform="translate(484 370) rotate(31) scale(0.66)" />
              <g class="blossom blossom--late">
                <use
                  href="#login-blossom"
                  transform="translate(417 215) rotate(-18) scale(0.58 0.51)"
                />
              </g>
              <g transform="translate(190 552) rotate(-32) scale(0.64)">
                <use href="#login-petal-shape" transform="rotate(-48) scale(0.7 0.8)" />
                <use href="#login-petal-shape" transform="rotate(16) scale(0.72 0.88)" />
                <use href="#login-petal-shape" transform="rotate(64) scale(0.65 0.72)" />
                <path
                  fill="url(#login-petal)"
                  stroke="var(--petal-line)"
                  stroke-width="0.65"
                  stroke-opacity="0.35"
                  d="M-46-31 C-28-38-11-29 0-20 C14-34 31-40 43-31 C38-2 18 15 0 20 C-20 12-39-4-46-31Z"
                />
                <path
                  fill="none"
                  stroke="var(--petal-line)"
                  stroke-width="0.65"
                  stroke-linecap="round"
                  opacity="0.18"
                  d="M-34-25 Q-22-2-4 12 M29-26 Q17-2 3 13"
                />
                <path
                  fill="url(#login-leaf)"
                  stroke="var(--leaf-deep)"
                  stroke-width="0.65"
                  stroke-opacity="0.35"
                  d="M-21 7 Q-8 12 0 20 Q11 10 22 5 Q16 26 0 28 Q-15 23-21 7Z"
                />
              </g>
              <g class="blossom">
                <use href="#login-blossom" transform="translate(320 390) rotate(-12)" />
              </g>
            </g>

            <g transform="translate(456 560) rotate(32)">
              <path class="drift" d="M0 0 C-15-5-19-21-10-27 C5-30 14-11 0 0Z" />
            </g>
            <g transform="translate(387 640) rotate(-24)">
              <path class="drift drift-late" d="M0 0 C-9-3-14-15-6-19 C6-19 10-6 0 0Z" />
            </g>
            <rect class="paper-grain" width="640" height="760" filter="url(#login-paper-grain)" />
          </svg>
        </div>

        <!-- 右侧表单 -->
        <div class="card-side is-form">
          <div class="form-wrapper">
            <header class="form-header">
              <!-- 如果是在初始化，显示特定标题 -->
              <h2 v-if="isFirstRun" class="init-title">系统初始化，请创建超级管理员</h2>
              <template v-else>
                <h2 class="welcome-title">欢迎登录</h2>
                <p class="welcome-subtitle">登录后继续使用 {{ brandName }}</p>
              </template>
            </header>

            <div class="login-content" :class="{ 'is-initializing': isFirstRun }">
              <!-- 初始化管理员表单 -->
              <div v-if="isFirstRun" class="login-form login-form--init">
                <a-form
                  :model="adminForm"
                  layout="vertical"
                  validate-trigger="submit"
                  @finish="handleInitialize"
                  @finish-failed="handleFormInvalid"
                >
                  <a-form-item
                    label="UID"
                    name="uid"
                    :rules="[
                      { required: true, message: '请输入UID' },
                      {
                        pattern: /^[a-zA-Z0-9_]+$/,
                        message: 'UID只能包含字母、数字和下划线'
                      },
                      {
                        min: 3,
                        max: 20,
                        message: 'UID长度必须在3-20个字符之间'
                      }
                    ]"
                  >
                    <a-input
                      v-model:value="adminForm.uid"
                      placeholder="请输入UID（3-20个字符）"
                      :maxlength="20"
                    />
                  </a-form-item>

                  <a-form-item
                    label="手机号（可选）"
                    name="phone_number"
                    :rules="[
                      {
                        validator: async (rule, value) => {
                          if (!value || value.trim() === '') {
                            return // 空值允许
                          }
                          const phoneRegex = /^1[3-9]\d{9}$/
                          if (!phoneRegex.test(value)) {
                            throw new Error('请输入正确的手机号格式')
                          }
                        }
                      }
                    ]"
                  >
                    <a-input
                      v-model:value="adminForm.phone_number"
                      placeholder="可用于登录，可不填写"
                      :max-length="11"
                    />
                  </a-form-item>

                  <a-form-item
                    label="密码"
                    name="password"
                    :rules="[
                      { required: true, message: '请输入密码' },
                      {
                        min: MIN_PASSWORD_LENGTH,
                        message: `密码至少需要 ${MIN_PASSWORD_LENGTH} 个字符`
                      }
                    ]"
                  >
                    <a-input-password
                      v-model:value="adminForm.password"
                      prefix-icon="lock"
                      :minlength="MIN_PASSWORD_LENGTH"
                    />
                  </a-form-item>

                  <a-form-item
                    label="确认密码"
                    name="confirmPassword"
                    :rules="[
                      { required: true, message: '请确认密码' },
                      { validator: validateConfirmPassword }
                    ]"
                  >
                    <a-input-password
                      v-model:value="adminForm.confirmPassword"
                      prefix-icon="lock"
                    />
                  </a-form-item>

                  <a-form-item v-if="showAgreementConsent" class="agreement-form-item">
                    <div class="agreement-row">
                      <a-checkbox v-model:checked="agreementAccepted">
                        登录即代表同意
                        <a
                          class="agreement-link"
                          :href="userAgreementUrl"
                          target="_blank"
                          rel="noopener noreferrer"
                          @click.stop
                          >《用户协议》</a
                        >
                        <a
                          class="agreement-link"
                          :href="privacyPolicyUrl"
                          target="_blank"
                          rel="noopener noreferrer"
                          @click.stop
                          >《隐私协议》</a
                        >
                      </a-checkbox>
                    </div>
                  </a-form-item>

                  <a-form-item>
                    <a-button type="primary" html-type="submit" :loading="loading" block
                      >创建管理员账户</a-button
                    >
                  </a-form-item>
                </a-form>
              </div>

              <!-- 登录表单 -->
              <div v-else class="login-form">
                <a-form
                  :model="loginForm"
                  layout="vertical"
                  validate-trigger="submit"
                  @finish="handleLogin"
                  @finish-failed="handleFormInvalid"
                >
                  <a-form-item
                    label="登录账号"
                    name="loginId"
                    :rules="[{ required: true, message: '请输入UID或手机号' }]"
                  >
                    <a-input v-model:value="loginForm.loginId" placeholder="UID或手机号">
                      <template #prefix>
                        <user-icon size="18" />
                      </template>
                    </a-input>
                  </a-form-item>

                  <a-form-item
                    label="密码"
                    name="password"
                    :rules="[{ required: true, message: '请输入密码' }]"
                  >
                    <a-input-password v-model:value="loginForm.password">
                      <template #prefix>
                        <lock-icon size="18" />
                      </template>
                    </a-input-password>
                  </a-form-item>

                  <a-form-item v-if="showAgreementConsent" class="agreement-form-item">
                    <div class="agreement-row">
                      <a-checkbox v-model:checked="agreementAccepted">
                        登录即代表同意
                        <a
                          class="agreement-link"
                          :href="userAgreementUrl"
                          target="_blank"
                          rel="noopener noreferrer"
                          @click.stop
                          >《用户协议》</a
                        >
                        <a
                          class="agreement-link"
                          :href="privacyPolicyUrl"
                          target="_blank"
                          rel="noopener noreferrer"
                          @click.stop
                          >《隐私协议》</a
                        >
                      </a-checkbox>
                    </div>
                  </a-form-item>

                  <a-form-item>
                    <a-button
                      type="primary"
                      html-type="submit"
                      :loading="loading"
                      :disabled="isLocked"
                      block
                      size="large"
                    >
                      <span v-if="isLocked">账户已锁定 {{ formatTime(lockRemainingTime) }}</span>
                      <span v-else>登录</span>
                    </a-button>
                  </a-form-item>
                </a-form>

                <!-- OIDC 登录选项  -->
                <div v-if="oidcChecking || oidcEnabled" class="third-party-login">
                  <div class="divider">
                    <span>或使用以下方式登录</span>
                  </div>
                  <div class="login-icons">
                    <!-- 检查中显示骨架屏 -->
                    <div v-if="oidcChecking" class="login-skeleton">
                      <a-skeleton-button block size="large" :active="true" />
                    </div>
                    <!-- 检查完成后显示按钮 -->
                    <a-button
                      v-else
                      type="default"
                      size="large"
                      block
                      :loading="oidcLoading"
                      @click="handleOIDCLogin"
                    >
                      <template #icon>
                        <key-icon size="18" />
                      </template>
                      {{ oidcButtonText }}
                    </a-button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </main>

    <!-- 页面底部：版权信息等 -->
    <footer class="page-footer">
      <div class="footer-links">
        <a href="https://github.com/xerrors" target="_blank">联系我们</a>
        <span class="divider">|</span>
        <a href="https://github.com/xerrors/Yuxi" target="_blank">使用帮助</a>
      </div>
      <div class="copyright">
        &copy; {{ new Date().getFullYear() }} {{ brandName }}. All Rights Reserved.
      </div>
    </footer>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, onUnmounted, computed, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useUserStore } from '@/modules/identity/model/user'
import { useInfoStore } from '@/modules/settings/model/info'
import { useAgentStore } from '@/modules/agents/model/agent'
import { message } from 'ant-design-vue'
import { healthApi } from '@/apis/system_api'
import { authApi } from '@/apis/auth_api'
import {
  User as UserIcon,
  Lock as LockIcon,
  Key as KeyIcon,
  AlertCircle as ExclamationCircleIcon
} from '@lucide/vue'
import { tryAutoStartOIDC, sanitizeRedirect } from '@/modules/identity/model/oidcAutoStart'
import { MIN_PASSWORD_LENGTH } from '@/modules/identity/model/passwordValidation'

const router = useRouter()
const route = useRoute()
const userStore = useUserStore()
const infoStore = useInfoStore()
const agentStore = useAgentStore()

// 历史默认图片不再随包提供；若部署者沿用同一路径配置图片，仍优先显示该图片。
const STOCK_LOGIN_BG = '/login-bg.jpg'
const legacyLoginBgMissing = ref(false)
watch(() => infoStore.branding.login_bg, () => { legacyLoginBgMissing.value = false })
const customLoginBg = computed(() => {
  const value = infoStore.branding.login_bg?.trim() || ''
  if (value === STOCK_LOGIN_BG && legacyLoginBgMissing.value) return ''
  return value
})
const handleLoginBgError = () => {
  if (customLoginBg.value === STOCK_LOGIN_BG) legacyLoginBgMissing.value = true
}
const brandLogo = computed(() => {
  return infoStore.branding.logo || ''
})
const brandName = computed(() => infoStore.branding.name?.trim() || 'Yuxi')
const userAgreementUrl = computed(() => {
  return infoStore.footer?.user_agreement_url?.trim() || ''
})
const privacyPolicyUrl = computed(() => {
  return infoStore.footer?.privacy_policy_url?.trim() || ''
})
const showAgreementConsent = computed(() => {
  return Boolean(userAgreementUrl.value && privacyPolicyUrl.value)
})

// 状态
const isFirstRun = ref(false)
const loading = ref(false)
const agreementAccepted = ref(false)
const serverStatus = ref('loading')
const serverError = ref('')
const healthChecking = ref(false)

// OIDC 相关状态
const oidcEnabled = ref(false)
const oidcLoading = ref(false)
const oidcChecking = ref(true)
const oidcButtonText = ref('OIDC 登录')

// 登录锁定相关状态
const isLocked = ref(false)
const lockRemainingTime = ref(0)
const lockCountdown = ref(null)

// 登录表单
const loginForm = reactive({
  loginId: '', // 支持uid或phone_number登录
  password: ''
})

// 管理员初始化表单
const adminForm = reactive({
  uid: '', // 改为直接输入uid
  password: '',
  confirmPassword: '',
  phone_number: '' // 手机号字段（可选）
})

const goHome = () => {
  router.push('/')
}

// 清理倒计时器
const clearLockCountdown = () => {
  if (lockCountdown.value) {
    clearInterval(lockCountdown.value)
    lockCountdown.value = null
  }
}

// 启动锁定倒计时
const startLockCountdown = (remainingSeconds) => {
  clearLockCountdown()
  isLocked.value = true
  lockRemainingTime.value = remainingSeconds

  lockCountdown.value = setInterval(() => {
    lockRemainingTime.value--
    if (lockRemainingTime.value <= 0) {
      clearLockCountdown()
      isLocked.value = false
    }
  }, 1000)
}

// 格式化时间显示
const formatTime = (seconds) => {
  if (seconds < 60) {
    return `${seconds}秒`
  } else if (seconds < 3600) {
    const minutes = Math.floor(seconds / 60)
    const remainingSeconds = seconds % 60
    return `${minutes}分${remainingSeconds}秒`
  } else if (seconds < 86400) {
    const hours = Math.floor(seconds / 3600)
    const minutes = Math.floor((seconds % 3600) / 60)
    return `${hours}小时${minutes}分钟`
  } else {
    const days = Math.floor(seconds / 86400)
    const hours = Math.floor((seconds % 86400) / 3600)
    return `${days}天${hours}小时`
  }
}

// 密码确认验证
const validateConfirmPassword = async (rule, value) => {
  if (value === '') {
    throw new Error('请确认密码')
  }
  if (value !== adminForm.password) {
    throw new Error('两次输入的密码不一致')
  }
}

const showLoginError = (content) => {
  const text = typeof content === 'string' ? content.trim() : ''
  if (text) {
    message.error(text)
  }
}

const handleFormInvalid = ({ errorFields } = {}) => {
  const errors = [
    ...new Set((errorFields || []).flatMap((field) => field.errors || []).filter(Boolean))
  ]
  showLoginError(errors.join('；') || '请检查输入内容')
}

const ensureAgreementAccepted = () => {
  if (!showAgreementConsent.value || agreementAccepted.value) {
    return true
  }

  message.warning('请先阅读并同意《用户协议》《隐私协议》')
  return false
}

// 处理登录
const handleLogin = async () => {
  // 如果当前被锁定，不允许登录
  if (isLocked.value) {
    message.warning(`账户被锁定，请等待 ${formatTime(lockRemainingTime.value)}`)
    return
  }

  if (!ensureAgreementAccepted()) {
    return
  }

  try {
    loading.value = true
    clearLockCountdown()

    await userStore.login({
      loginId: loginForm.loginId,
      password: loginForm.password
    })

    message.success('登录成功')

    // 获取重定向路径
    const redirectPath = sessionStorage.getItem('redirect') || '/'
    sessionStorage.removeItem('redirect') // 清除重定向信息

    // 根据用户角色决定重定向目标
    if (redirectPath === '/') {
      // 统一跳转到聊天页面（管理员与普通用户共享同一聊天界面）
      try {
        await agentStore.initialize()
        router.push('/agent')
      } catch (error) {
        console.error('获取智能体信息失败:', error)
        router.push('/agent')
      }
    } else {
      // 跳转到其他预设的路径
      router.push(redirectPath)
    }
  } catch (error) {
    console.error('登录失败:', error)

    // 检查是否是锁定错误（HTTP 423）
    if (error.status === 423) {
      // 尝试从响应头中获取剩余时间
      let remainingTime = 0
      if (error.headers && error.headers.get) {
        const lockRemainingHeader = error.headers.get('X-Lock-Remaining')
        if (lockRemainingHeader) {
          remainingTime = parseInt(lockRemainingHeader)
        }
      }

      // 如果没有从头中获取到，尝试从错误消息中解析
      if (remainingTime === 0) {
        const lockTimeMatch = error.message.match(/(\d+)\s*秒/)
        if (lockTimeMatch) {
          remainingTime = parseInt(lockTimeMatch[1])
        }
      }

      if (remainingTime > 0) {
        startLockCountdown(remainingTime)
        showLoginError(`由于多次登录失败，账户已被锁定 ${formatTime(remainingTime)}`)
      } else {
        showLoginError(error.message || '账户被锁定，请稍后再试')
      }
    } else {
      showLoginError(error.message || '登录失败，请检查用户名和密码')
    }
  } finally {
    loading.value = false
  }
}

// 处理 OIDC 登录
const handleOIDCLogin = async () => {
  if (!ensureAgreementAccepted()) {
    return
  }

  try {
    oidcLoading.value = true

    // 获取 OIDC 登录 URL
    const response = await authApi.getOIDCLoginUrl()
    if (response.login_url) {
      // 保存当前路径，以便登录后返回
      const redirectPath =
        sessionStorage.getItem('redirect') || router.currentRoute.value.query.redirect || '/'
      sessionStorage.setItem('oidc_redirect', redirectPath)

      // 跳转到 OIDC Provider
      window.location.href = response.login_url
    } else {
      showLoginError('获取 OIDC 登录地址失败')
    }
  } catch (error) {
    console.error('OIDC 登录失败:', error)
    showLoginError(error.message || 'OIDC 登录失败，请重试')
  } finally {
    oidcLoading.value = false
  }
}

// 检查 OIDC 配置
const checkOIDCConfig = async () => {
  oidcChecking.value = true
  try {
    const config = await authApi.getOIDCConfig()
    oidcEnabled.value = config.enabled
    if (config.provider_name) {
      oidcButtonText.value = config.provider_name
    }
    return config
  } catch (error) {
    console.error('检查 OIDC 配置失败:', error)
    oidcEnabled.value = false
    return null
  } finally {
    oidcChecking.value = false
  }
}

// 处理初始化管理员
const handleInitialize = async () => {
  if (!ensureAgreementAccepted()) {
    return
  }

  try {
    loading.value = true

    if (adminForm.password !== adminForm.confirmPassword) {
      showLoginError('两次输入的密码不一致')
      return
    }

    await userStore.initialize({
      uid: adminForm.uid,
      password: adminForm.password,
      phone_number: adminForm.phone_number || null // 空字符串转为null
    })

    message.success('管理员账户创建成功')
    router.push('/')
  } catch (error) {
    console.error('初始化失败:', error)
    showLoginError(error.message || '初始化失败，请重试')
  } finally {
    loading.value = false
  }
}

// 检查是否是首次运行
const checkFirstRunStatus = async () => {
  try {
    loading.value = true
    const isFirst = await userStore.checkFirstRun()
    isFirstRun.value = isFirst
  } catch (error) {
    console.error('检查首次运行状态失败:', error)
    showLoginError('系统出错，请稍后重试')
  } finally {
    loading.value = false
  }
}

// 检查服务器健康状态
const checkServerHealth = async () => {
  try {
    healthChecking.value = true
    const response = await healthApi.checkHealth()
    if (response.status === 'ok') {
      serverStatus.value = 'ok'
    } else {
      serverStatus.value = 'error'
      serverError.value = response.message || '服务端状态异常'
    }
  } catch (error) {
    console.error('检查服务器健康状态失败:', error)
    serverStatus.value = 'error'
    serverError.value = error.message || '无法连接到服务端，请检查网络连接'
  } finally {
    healthChecking.value = false
  }
}

// 组件挂载时
onMounted(async () => {
  // 如果已登录，按 redirect 参数跳转（不固定跳首页）
  if (userStore.isLoggedIn) {
    router.push(sanitizeRedirect(route.query.redirect))
    return
  }

  // 显示 OIDC 认证失败的错误信息（由后端重定向携带）
  if (route.query.oidc_error) {
    showLoginError(String(route.query.oidc_error))
  }

  // 首先检查服务器健康状态
  await checkServerHealth()

  // 检查是否是首次运行
  await checkFirstRunStatus()

  // 如果处于首次运行状态，不需要 OIDC 自动登录
  if (isFirstRun.value) {
    return
  }

  // 检查 OIDC 配置完成后，尝试自动触发 OIDC 登录（跨系统跳转场景）
  const config = await checkOIDCConfig()
  if (config && config.enabled) {
    const autoStarted = await tryAutoStartOIDC(async () => await authApi.getOIDCLoginUrl(), config)
    // 如果已发起 OIDC 跳转，页面会被重定向，不需要继续
    if (autoStarted) return
  }
})

// 组件卸载时清理定时器
onUnmounted(() => {
  clearLockCountdown()
})
</script>

<style lang="less" scoped>
.login-view {
  min-height: 100vh;
  width: 100%;
  position: relative;
  display: flex;
  flex-direction: column;
  background-color: var(--gray-10);
  background-image: radial-gradient(
    color-mix(in srgb, var(--gray-300) 40%, transparent) 0.7px,
    transparent 0.8px
  );
  background-size: 28px 28px;

  &.has-alert {
    padding-top: 60px;
  }
}

/* Unified Navbar */
.login-navbar {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  padding: 32px 0;
  z-index: 10;

  .navbar-content {
    max-width: 1500px; /* Constraint the width */
    margin: 0 auto;
    padding: 0 40px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    .brand-container {
      display: flex;
      align-items: center;
      gap: 12px;

      /* 可点击返回首页：hover 仅加深品牌文字，不做位移 */
      &:hover .brand-main {
        color: var(--gray-900);
      }
    }
  }
}

.brand-text {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  line-height: 1;
  display: flex;
  align-items: center;
  gap: 12px;

  .brand-main {
    color: var(--gray-700);
    font-weight: 600;
    transition: color 0.2s ease;
  }
}

.brand-logo {
  height: 32px;
  width: auto;
  object-fit: contain;
}

.top-logo {
  height: 32px;
  width: auto;
  object-fit: contain;
}

.back-home-btn {
  color: var(--gray-600);
  font-size: 14px;
  &:hover {
    color: var(--main-color);
    background-color: transparent;
  }
}

/* Main Content: Card Layout */
.login-main {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  padding-top: 80px; /* Add space for navbar */
}

.login-card {
  width: 900px;
  height: 560px;
  flex: 0 0 auto;
  background: var(--gray-0);
  border-radius: 16px;
  border: 1px solid var(--border-surface);
  /* 独立悬浮卡片的极轻环境光，压暗主题下 token 自动减弱 */
  box-shadow:
    0 1px 2px var(--shadow-1),
    0 12px 32px -16px var(--shadow-2);
  display: flex;
  overflow: hidden;
}

.card-side {
  position: relative;
}

/* Image Side */
.card-side.is-image {
  flex: 1.4;
  background-color: var(--gray-0);
  border-right: 1px solid var(--border-surface);
  overflow: hidden;

  .login-bg-image,
  .login-bg-art {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
  }

  .login-bg-image {
    object-fit: cover;
    object-position: center;
  }

  .login-bg-art {
    display: block;
    pointer-events: none;
    --paper-light: #fffefa;
    --paper: #faf8f0;
    --wash: #ebeddf;
    --wood: #726751;
    --wood-light: #a29577;
    --leaf: #819782;
    --leaf-light: #b2bba1;
    --leaf-deep: #526e61;
    --leaf-vein: #e1e3c9;
    --petal: #fff7f0;
    --petal-mid: #f0cfc6;
    --petal-deep: #d99c98;
    --petal-line: #c18e8a;
    --petal-rim: #fffaf4;
    --heart: #ad7370;
    --stamen: #ab874b;
    --pollen: #d9b96d;
  }

  .wood {
    fill: url(#login-wood);
  }

  .twig,
  .wood-grain {
    fill: none;
    stroke-linecap: round;
  }

  .twig {
    stroke: var(--wood-light);
    stroke-width: 1.7;
  }

  .wood-grain {
    stroke: var(--paper-light);
    stroke-width: 0.8;
    opacity: 0.3;
  }

  .distant-sprig {
    opacity: 0.075;
  }

  .paper-grain {
    opacity: 0.02;
    mix-blend-mode: multiply;
  }

  /* 背景光斑极慢漂移，只做明暗呼吸，不改变画面重心。 */
  .wash {
    transform-box: fill-box;
    transform-origin: center;
    animation: login-wash-breathe 26s ease-in-out infinite alternate;
  }

  .wash--late {
    animation-duration: 34s;
    animation-delay: -13s;
  }

  /* 花朵随枝干摆动之外再轻微开合，幅度低于视觉可辨识的形变阈值。 */
  .blossom {
    transform-box: fill-box;
    transform-origin: center;
    animation: login-blossom-breathe 11s ease-in-out infinite alternate;
  }

  .blossom--late {
    animation-delay: -5.5s;
  }

  .bough {
    transform-box: view-box;
    transform-origin: 158px 790px;
    animation: login-bough-sway 18s ease-in-out infinite alternate;
  }

  .drift {
    fill: url(#login-petal);
    opacity: 0.6;
    transform-box: fill-box;
    transform-origin: center;
    animation: login-petal-drift 22s ease-in-out infinite;
  }

  .drift-late {
    animation-delay: -11s;
  }
}

@keyframes login-bough-sway {
  from {
    transform: rotate(-0.45deg);
  }
  to {
    transform: rotate(0.55deg);
  }
}

@keyframes login-petal-drift {
  0% {
    transform: translate(0, -8px) rotate(-6deg);
    opacity: 0;
  }
  25%,
  65% {
    opacity: 0.6;
  }
  100% {
    transform: translate(12px, 24px) rotate(18deg);
    opacity: 0;
  }
}

@keyframes login-wash-breathe {
  from {
    transform: translate(-6px, 4px) scale(1);
  }
  to {
    transform: translate(8px, -6px) scale(1.04);
  }
}

@keyframes login-blossom-breathe {
  from {
    transform: rotate(-0.35deg) scale(1);
  }
  to {
    transform: rotate(0.35deg) scale(1.012);
  }
}

@media (prefers-reduced-motion: reduce) {
  .card-side.is-image .bough,
  .card-side.is-image .drift,
  .card-side.is-image .wash,
  .card-side.is-image .blossom {
    animation: none;
  }
}

:global(:root.dark .card-side.is-image .login-bg-art) {
  --paper-light: #242d2a;
  --paper: #1b2422;
  --wash: #59664e;
  --wood: #8c8970;
  --wood-light: #b4b096;
  --leaf: #7f9481;
  --leaf-light: #a5b097;
  --leaf-deep: #4e695d;
  --leaf-vein: #d1d7ba;
  --petal: #e5d5ca;
  --petal-mid: #c7a19a;
  --petal-deep: #a77072;
  --petal-line: #8f6869;
  --petal-rim: #f2e3d5;
  --heart: #885a60;
  --stamen: #b59a64;
  --pollen: #dcc28a;
}

/* Form Side */
.card-side.is-form {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 40px;
}

.form-wrapper {
  width: 100%;
  max-width: 320px;
  display: flex;
  flex-direction: column;
  gap: 32px;
}

.form-header {
  text-align: left;
  .welcome-title {
    margin: 0;
    font-size: 20px;
    font-weight: 600;
    color: var(--gray-900);
    line-height: 1.4;
  }
  .welcome-subtitle {
    margin: 6px 0 0;
    font-size: 13px;
    color: var(--gray-500);
    line-height: 1.6;
  }
  .init-title {
    font-size: 18px;
    font-weight: 600;
    color: var(--main-color);
    margin: 0;
    line-height: 1.4;
  }
}

.login-form {
  /* 校验文案由 Message 展示，避免表单项说明撑开固定高度的卡片。 */
  :deep(.ant-form-item-explain) {
    display: none !important;
  }

  :deep(.ant-input-affix-wrapper) {
    padding: 10px 14px;
    border-radius: 8px;
  }
  :deep(.ant-btn) {
    height: 44px;
    font-size: 16px;
    border-radius: 8px;
  }
  :deep(.ant-input) {
    caret-color: var(--main-color);
  }
  :deep(.ant-input-prefix) {
    margin-right: 8px;
    color: var(--gray-400);
    transition: color 0.2s ease;
  }
  /* 聚焦时前缀图标随主色点亮，标示当前输入位置 */
  :deep(.ant-input-affix-wrapper-focused .ant-input-prefix),
  :deep(.ant-input-affix-wrapper:focus-within .ant-input-prefix) {
    color: var(--main-color);
  }
  :deep(.ant-form-item-label > label) {
    font-weight: 500;
  }
}

.login-form.login-form--init :deep(.ant-form-item) {
  margin-bottom: 14px;
}

.third-party-login {
  margin-top: 16px;
  .divider {
    display: flex;
    align-items: center;
    gap: 12px;
    margin: 24px 0 16px;
    &::before,
    &::after {
      content: '';
      flex: 1;
      height: 1px;
    }
    /* 两端渐隐的分割线，比硬截断更柔和 */
    &::before {
      background: linear-gradient(to right, transparent, var(--gray-200));
    }
    &::after {
      background: linear-gradient(to right, var(--gray-200), transparent);
    }
    span {
      color: var(--gray-400);
      font-size: 12px;
      white-space: nowrap;
    }
  }

  .login-icons {
    :deep(.ant-btn) {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      border-color: var(--gray-300);
      color: var(--gray-700);

      &:hover {
        border-color: var(--main-color);
        color: var(--main-color);
        background-color: var(--main-10);
      }

      .anticon,
      svg {
        color: var(--main-color);
      }
    }
  }

  /* 修复：添加骨架屏样式 */
  .login-skeleton {
    :deep(.ant-skeleton-button) {
      width: 100% !important;
      height: 44px;
      border-radius: 8px;
    }
  }
}

.agreement-form-item {
  margin-bottom: 12px;
}

.agreement-row {
  font-size: 13px;
  color: var(--gray-600);
  line-height: 1.6;

  :deep(.ant-checkbox-wrapper) {
    display: inline-flex;
    align-items: flex-start;
  }

  :deep(.ant-checkbox + span) {
    padding-inline-start: 8px;
  }
}

.agreement-link {
  color: var(--main-color);
  text-underline-offset: 3px;
  transition: color 0.2s ease;

  &:hover {
    color: var(--main-800);
    text-decoration: underline;
  }

  &:focus-visible {
    outline: 2px solid var(--main-color);
    outline-offset: 2px;
    border-radius: 2px;
  }
}

/* Page Footer */
.page-footer {
  padding: 24px;
  text-align: center;
}

.footer-links {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 16px;
  margin-bottom: 8px;

  a {
    color: var(--gray-500);
    font-size: 13px;
    text-underline-offset: 3px;
    transition: color 0.2s ease;

    &:hover {
      color: var(--main-color);
    }

    &:focus-visible {
      outline: 2px solid var(--main-color);
      outline-offset: 2px;
      border-radius: 2px;
    }
  }

  .divider {
    color: var(--gray-300);
    font-size: 12px;
  }
}

.copyright {
  font-size: 12px;
  color: var(--gray-400);
}

/* Server Status Alert */
.server-status-alert {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  padding: 12px 20px;
  background: var(--color-error-500);
  color: var(--gray-0);
  z-index: 1000;

  .alert-content {
    display: flex;
    align-items: center;
    max-width: 1500px;
    margin: 0 auto;

    .alert-icon {
      font-size: 20px;
      margin-right: 12px;
      color: var(--gray-0);
    }

    .alert-text {
      flex: 1;

      .alert-title {
        font-weight: 600;
        font-size: 16px;
        margin-bottom: 2px;
      }

      .alert-message {
        font-size: 14px;
        opacity: 0.9;
      }
    }

    :deep(.ant-btn-link) {
      color: var(--gray-0);
      border-color: var(--gray-0);

      &:hover {
        color: var(--gray-0);
        background-color: color-mix(in srgb, var(--gray-0) 10%, transparent);
      }
    }
  }
}

/* Responsive */
@media (max-width: 1280px) {
  .login-navbar .navbar-content {
    padding: 0 40px;
  }
}

@media (max-width: 768px) {
  .login-navbar .navbar-content {
    padding: 0 20px;
  }

  .brand-text {
    font-size: 20px;
  }

  .login-card {
    flex-direction: column;
    margin-top: 20px;
  }

  .card-side.is-image {
    display: none;
  }

  .card-side.is-form {
    padding: 40px 20px;
  }
}
</style>
