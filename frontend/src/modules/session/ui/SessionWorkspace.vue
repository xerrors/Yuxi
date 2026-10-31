<template>
  <div class="chat-container" :class="{ 'embedded-session': embedded, 'personal-mode': effectivePersonalMode }">
    <slot
      v-if="effectivePersonalMode"
      name="personal-actions"
      :sessions="cooperationSessions"
      :error="cooperationError"
      :refresh="refreshCooperation"
      :actions-target="personalActionsRef"
      :profile-dock-target="personalProfileEmbedded ? personalProfileDockRef : null"
    />
    <div
      class="chat"
      ref="workspaceChatRef"
      :id="workspacePanelId"
      :class="{
        'has-file-panel': isFilePanelOpen && !embedded,
        'has-maximized-panel': isFilePanelOpen && isAgentPanelMaximized,
        'is-resizing-file-panel': isResizing
      }"
      :style="{ '--file-panel-width': filePanelWidthStyle }"
    >
      <div
        v-if="effectivePersonalMode"
        v-show="!isAgentPanelMaximized"
        ref="personalActionsRef"
        class="personal-workspace-actions"
        :style="{
          right: `${localUIState.chatContentWidth - localUIState.chatMainVisibleWidth + 16}px`,
          '--personal-profile-max-width': `${Math.max(0, localUIState.chatMainVisibleWidth - 32)}px`
        }"
      />
      <div
        v-if="!embedded && !isAgentPanelMaximized && !effectivePersonalMode"
        class="chat-header"
        :class="{ 'has-active-thread': !!currentChatId }"
      >
        <div class="header__left">
          <slot name="header-left"></slot>
          <div
            v-if="currentThread?.yuxi.title && currentThread.yuxi.title !== '新的对话'"
            class="session-title"
          >
            {{ currentThread.yuxi.title }}
          </div>
        </div>
        <div class="header__right">
          <button
            v-if="!effectivePersonalMode && messageDebugEnabled"
            type="button"
            class="agent-nav-btn agent-debug-mode-btn"
            title="调试模式已开启：点击打开消息时序调试面板"
            @click.stop="toggleMessageDebugPanel"
          >
            <Bug size="15" class="nav-btn-icon debug-icon" />
            <span class="hide-text">Debug</span>
          </button>
          <button
            v-if="showStateEntry"
            type="button"
            class="agent-nav-btn agent-state-btn state-entry-btn"
            ref="mainStateTriggerRef"
            :class="{ active: statePanelOpen }"
            title="查看状态"
            :aria-expanded="statePanelOpen"
            aria-controls="agent-state-panel"
            @click.stop="toggleStatePanel"
          >
            <Gauge size="16" class="nav-btn-icon" />
            <span class="hide-text">状态</span>
          </button>
          <button
            v-if="!effectivePersonalMode && showFileEntry && !isFilePanelOpen"
            type="button"
            class="agent-nav-btn agent-state-btn file-entry-btn"
            title="打开侧边栏"
            aria-label="打开侧边栏"
            :aria-expanded="isFilePanelOpen"
            aria-controls="agent-file-panel"
            @click.stop="toggleAgentPanel"
          >
            <PanelRight size="16" class="nav-btn-icon" />
          </button>
          <slot
            name="header-right"
            :side-active="sideActive"
            :is-file-panel-open="isFilePanelOpen"
            :is-state-panel-open="statePanelOpen"
            :has-active-thread="!!currentChatId"
            :toggle-agent-panel="toggleAgentPanel"
          ></slot>
        </div>
      </div>

      <div
        ref="chatContentContainerRef"
        class="chat-content-container"
        :class="{
          'has-file-panel': isFilePanelOpen && !embedded
        }"
      >
        <!-- Main Chat Area -->
        <Teleport
          defer
          :to="`#${embedded ? workspacePanelId : mainChatPanelId}`"
          :disabled="!isAgentPanelMaximized"
        >
          <div
            class="chat-main"
            ref="chatMainRef"
            :class="{
              'is-loading-history': earlierMessagesLoading,
              'personal-chat-main': effectivePersonalMode,
              'has-personal-profile': personalProfileEmbedded,
              'has-embedded-state': statePanelOpen && statePanelPlacement.mode === 'embedded'
            }"
          >
            <div v-if="isSessionLoading" class="session-loading" role="status">
              <a-spin />
              <span>正在加载对话...</span>
            </div>
            <div class="chat-box">
              <div v-if="effectivePersonalMode && !isSessionLoading && !currentThreadHasHistory && !isReplyLoading" class="personal-chat-empty"><slot name="personal-empty">和助理聊聊你的想法，或告诉它你需要什么帮助。</slot></div>
              <button
                v-if="historyPages[currentChatId]?.hasMore"
                type="button"
                class="agent-nav-btn history-load-btn"
                :disabled="historyPages[currentChatId]?.loading || earlierMessagesLoading"
                :aria-busy="historyPages[currentChatId]?.loading || earlierMessagesLoading"
                @click="loadEarlierMessages"
              >
                {{ earlierMessagesLabel }}
              </button>
              <template v-for="row in messageGroupRows" :key="row.key">
                <div v-if="row.type === 'message-group'" class="group-box">
                  <div v-if="row.timeLabel" class="message-group-time">
                    {{ row.timeLabel }}
                  </div>
                  <div
                    v-for="(displayItem, itemIndex) in row.displayItems"
                    :key="displayItem.key"
                    class="history-display-item"
                    :data-history-key="displayItem.key"
                  >
                    <button
                      v-if="effectivePersonalMode && displayItem.message?.type === 'created-session'"
                      type="button"
                      class="personal-session-bubble"
                      :data-session-id="displayItem.message.session.id"
                      :aria-label="`查看新建对话：${displayItem.message.session.name}`"
                      @click="navigateToSession(displayItem.message.session.id)"
                    >
                      <MessageSquarePlus :size="16" aria-hidden="true" />
                      <span class="personal-session-label">新建对话</span>
                      <span class="personal-session-name" :title="displayItem.message.session.name">{{ displayItem.message.session.name }}</span>
                      <ArrowUpRight :size="14" aria-hidden="true" />
                    </button>
                    <AgentMessageComponent
                      v-else-if="displayItem.type === 'message'"
                      :message="displayItem.message"
                      :thread-id="currentChatId"
                      :is-processing="isDisplayMessageProcessing(row.group, displayItem)"
                      :show-refs="showMsgRefs(displayItem.message, row.group)"
                      :hide-tool-calls="true"
                      :mention="effectivePersonalMode ? null : mentionConfig"
                      :widget-action="handleWidgetAction"
                      :widgets-disabled="readOnly || row.group.status === 'streaming' || widgetActionsDisabled"
                      @retry="retryMessage(displayItem.message)"
                      @open-refs="openTurnSourcesPanel"
                      @open-session="navigateToSession"
                    >
                    </AgentMessageComponent>
                    <ToolCallsGroupComponent
                      v-else-if="!effectivePersonalMode && displayItem.type === 'tool-group'"
                      :tool-calls="displayItem.toolCalls"
                      :entries="displayItem.entries"
                      :cooperation-view="cooperationToolView"
                      :is-active="isToolGroupActive(row.group, itemIndex, row.displayItems)"
                    />
                    <RunProcessGroupComponent
                      v-else-if="!effectivePersonalMode"
                      :items="displayItem.items"
                      :cooperation-view="cooperationToolView"
                      :message-count="displayItem.messageCount"
                      :tool-call-count="displayItem.toolCallCount"
                      :duration-ms="displayItem.durationMs"
                      :widgets-disabled="readOnly || row.group.status === 'streaming' || widgetActionsDisabled"
                      :mention="effectivePersonalMode ? null : mentionConfig"
                      :widget-action="handleWidgetAction"
                    />
                  </div>
                  <RunFailureNotice
                    v-if="row.group.run?.status === 'failed'"
                    :error-message="row.group.run.error_message"
                  />
                  <div
                    v-else-if="!row.displayItems.length && row.group.run"
                    class="chat-inline-notice"
                  >
                    {{ formatEmptyRunStatus(row.group.run.status) }}
                  </div>
                  <AgentArtifactsCard
                    v-if="row.artifacts.length && shouldShowRefs(row.group)"
                    :artifacts="row.artifacts"
                    :thread-id="currentChatId"
                    @saved="handleArtifactSaved"
                    @open-preview="openPanelPreview"
                  />
                  <!-- 显示对话最后一个消息使用的模型 -->
                  <RefsComponent
                    v-if="shouldShowRefs(row.group)"
                    :thread-id="currentChatId"
                    :message="getLastMessage(row.group)"
                    :run="getMessageRun(getLastMessage(row.group))"
                    :show-refs="effectivePersonalMode ? ['copy', 'sources'] : ['model', 'copy', 'sources']"
                    :is-latest-message="false"
                    :sources="getMessageGroupSources(row.group)"
                    @open-refs="openTurnSourcesPanel"
                  />
                </div>
                <div v-else class="chat-inline-notice">
                  <span>{{ row.notice.message }}</span>
                </div>
              </template>

              <div class="generating-status" v-if="isReplyLoading && !isSessionLoading" role="status">
                <div class="generating-indicator">
                  <span class="loading-spark" aria-hidden="true"></span>
                  <span class="generating-text">{{ replyLoadingText }}</span>
                  <button
                    v-if="!effectivePersonalMode && !readOnly && primaryQueuedInput && !currentThreadState?.activeRunId"
                    type="button"
                    class="generating-cancel lucide-icon-btn"
                    aria-label="取消这条待处理输入"
                    title="取消这条待处理输入"
                    :disabled="cancellingInputIds.has(primaryQueuedInput.input_id)"
                    @click="handleCancelQueuedInput(primaryQueuedInput.input_id)"
                  >
                    <X :size="14" />
                  </button>
                </div>
              </div>
            </div>
            <div
              ref="messageInputDockRef"
              class="bottom"
              v-if="!readOnly || currentApprovalModalVisible"
              :class="{ 'start-screen': showStartScreen }"
            >
              <div class="message-input-wrapper">
                <div v-if="pendingSends[currentChatId] && !pendingSends[currentChatId].submitting" class="queued-request-notice">
                  <span>发送结果待确认，重试会确认同一条消息。</span>
                  <button
                    class="queued-request-continue"
                    :disabled="!agentExecutionAvailable || sendCooldownActive || pendingSends[currentChatId]?.submitting"
                    @click="handleSendMessage({ retry: true })"
                  >
                    重试发送
                  </button>
                </div>
                <!-- 打招呼区域 - 在输入框上方 -->
                <div v-if="showStartScreen" class="chat-greeting-input">
                  <h1>{{ randomGreeting }}</h1>
                </div>

                <section
                  v-if="!effectivePersonalMode && !readOnly && visibleQueuedInputs.length"
                  class="queued-request-panel"
                  aria-label="排队请求"
                >
                  <div
                    v-if="currentQueueSnapshot.status === 'paused'"
                    class="queued-request-notice is-paused"
                  >
                    <span>{{ queuePausedMessage }}</span>
                    <button
                      type="button"
                      class="queued-request-continue"
                      :disabled="!agentExecutionAvailable || currentThreadState?.continueQueueInFlight"
                      @click="handleContinueQueue"
                    >
                      <Play :size="14" fill="currentColor" />
                      继续队列
                    </button>
                  </div>
                  <div v-else-if="isWaitingForUserAction" class="queued-request-notice">
                    当前任务正在等待回答或审批，完成后将继续处理后续输入。
                  </div>
                  <div class="queued-request-list">
                    <div
                      v-for="input in visibleQueuedInputs"
                      :key="input.input_id"
                      class="queued-request-row"
                    >
                      <CornerDownRight :size="16" class="queued-request-icon" aria-hidden="true" />
                      <span class="queued-request-content" :title="input.content || '排队输入'">
                        {{ input.content || '排队输入' }}
                      </span>
                      <span class="queued-request-status" :title="input.attachment_error || ''">
                        {{
                          input.attachment_status === 'preparing'
                            ? '附件准备中'
                            : input.kind === 'steer'
                              ? '等待引导'
                              : '排队中'
                        }}
                      </span>
                      <div class="queued-request-actions">
                        <button
                          v-if="canPromoteQueuedInput(input)"
                          type="button"
                          class="queued-request-promote lucide-icon-btn"
                          :disabled="
                            promotingInputIds.has(input.input_id) ||
                            cancellingInputIds.has(input.input_id)
                          "
                          :title="
                            isStreaming ? '转为引导，将沿用当前任务的模型和审批配置' : '转为引导'
                          "
                          aria-label="转为引导"
                          @click="handlePromoteQueuedInput(input.input_id)"
                        >
                          <ArrowUpFromLine :size="16" />
                        </button>
                        <button
                          v-if="canCancelQueuedInput(input)"
                          type="button"
                          class="queued-request-delete lucide-icon-btn"
                          :disabled="cancellingInputIds.has(input.input_id)"
                          :aria-label="`取消排队输入：${input.content || '排队输入'}`"
                          @click="handleCancelQueuedInput(input.input_id)"
                        >
                          <Trash2 :size="16" />
                        </button>
                      </div>
                    </div>
                  </div>
                </section>

                <CooperationAttention
                  v-if="!embedded && !effectivePersonalMode"
                  :key="currentChatId"
                  :actions="cooperationPendingActions"
                  :error="cooperationError"
                  @open="openCooperationAttention"
                  @refresh="refreshCooperation"
                />

                <div
                  ref="messageInputStageRef"
                  class="message-input-stage"
                  :class="{ 'has-tool-approval': currentToolApprovalVisible }"
                >
                  <div v-if="currentChatId && ['unavailable', 'error'].includes(sessionAgentStatus)" class="agent-availability-notice" role="status">
                    <span v-if="sessionAgentStatus === 'unavailable'">智能体当前不可用</span>
                    <template v-else-if="sessionAgentStatus === 'error'">
                      <span>智能体加载失败，请重试</span>
                      <a-button type="link" size="small" @click="retrySessionAgent">重试</a-button>
                    </template>
                  </div>
                  <HumanApprovalModal
                    v-if="agentExecutionAvailable"
                    :visible="currentApprovalModalVisible"
                    :processing="approvalSubmitting"
                    :questions="currentApprovalQuestions"
                    :kind="approvalState.kind"
                    :action-requests="approvalState.actionRequests"
                    @submit="handleQuestionSubmit"
                    @cancel="handleQuestionCancel"
                  />

                  <div
                    class="message-input-surface"
                    :inert="currentToolApprovalVisible"
                    :aria-hidden="currentToolApprovalVisible ? 'true' : undefined"
                  >
                    <AgentInputArea
                      v-if="!readOnly"
                      ref="agentInputAreaRef"
                      v-model="userInput"
                      :is-loading="shouldShowStopButton"
                      :disabled="threadCreationInFlight || !agentExecutionAvailable || currentToolApprovalVisible"
                      :send-button-disabled="isSendButtonDisabled"
                      :mention="effectivePersonalMode ? null : mentionConfig"
                      :thread-id="currentChatId"
                      :personal-mode="effectivePersonalMode"
                      :show-extra="showStartScreen"
                      :attachments="currentPendingThreadAttachments"
                      @send="handleSendOrStop"
                      @upload-attachment="handleAttachmentUpload"
                      @remove-attachment="handleAttachmentRemove"
                    >
                      <template #extra>
                        <ProjectSelectionSection
                          upward
                          v-if="showStartScreen"
                          v-model="selectedProjectId"
                          :disabled="threadCreationInFlight"
                        />
                      </template>
                      <template #actions-left-extra>
                        <ToolApprovalModeSelector
                          v-if="!effectivePersonalMode"
                          upward
                          :model-value="currentToolApprovalMode"
                          @update:model-value="handleToolApprovalModeSelect"
                        />
                        <slot
                          v-if="!effectivePersonalMode"
                          name="input-actions-left"
                          :agent-id="currentAgentId"
                          :has-active-thread="!!currentChatId"
                          :is-creating-thread="threadCreationInFlight"
                        ></slot>
                      </template>
                      <template #actions-right-extra>
                        <button
                          v-if="!effectivePersonalMode && canSubmitSteer"
                          type="button"
                          class="direct-steer-button"
                          title="当前步骤结束后优先执行这条消息"
                          @click="handleDirectSteer"
                        >
                          <CornerDownRight :size="14" aria-hidden="true" />
                          引导
                        </button>
                        <ContextUsageRing
                          v-if="!effectivePersonalMode && showStateEntry"
                          :used-tokens="contextUsageSummary.usedTokens"
                          :limit-tokens="contextUsageSummary.limitTokens"
                          :ratio="contextUsageSummary.ratio"
                          @click="toggleStatePanel"
                        />
                        <div v-if="!effectivePersonalMode" class="input-model-selector">
                          <ModelSelectorComponent
                            upward
                            :model_spec="currentModelSpec"
                            size="nano"
                            display-name="mini"
                            placeholder="选择模型"
                            @select-model="handleModelSelect"
                          />
                        </div>
                        <slot
                          name="input-actions-right"
                          :has-active-thread="!!currentChatId"
                        ></slot>
                      </template>
                    </AgentInputArea>
                  </div>
                </div>

                <AttachmentTmpUploadModal
                  v-model:open="attachmentUploadModalOpen"
                  :initial-files="attachmentInitialFiles"
                  :initial-files-key="attachmentInitialFilesKey"
                  @added="handleTmpAttachmentsAdded"
                />

                <div class="bottom-actions" v-if="!showStartScreen && !effectivePersonalMode">
                  <p class="note">
                    当前智能体：{{ currentThreadAgentName }}；请注意辨别内容的可靠性
                  </p>
                </div>
              </div>
            </div>
          </div>
        </Teleport>
        <div
          v-show="personalProfileEmbedded"
          ref="personalProfileDockRef"
          class="personal-profile-dock"
          :style="{ right: `${localUIState.chatContentWidth - localUIState.chatMainVisibleWidth + 24}px` }"
        />
      </div>

      <div
        v-if="!embedded"
        id="agent-state-panel"
        ref="statePanelRef"
        :inert="!statePanelOpen"
        :aria-hidden="!statePanelOpen"
        class="side-panel side-panel--state"
        :class="{
          'is-visible': statePanelOpen,
          'is-embedded': statePanelPlacement.mode === 'embedded'
        }"
        :style="statePanelPlacement.style"
      >
        <div
          class="state-panel"
          :style="{
            maxHeight: statePanelPlacement.style.maxHeight
          }"
        >
          <div class="side-panel__header state-panel-header">
            <span class="state-panel-title">状态</span>
            <button
              type="button"
              class="state-refresh-btn"
              title="刷新状态"
              aria-label="刷新状态"
              :disabled="isRefreshingState"
              @click.stop="handleAgentStateRefresh()"
            >
              <RefreshCw :size="14" :class="{ 'is-spinning': isRefreshingState }" />
            </button>
          </div>

          <div class="state-panel-body">
            <section v-if="currentAgentState?.warnings?.length" class="state-warning" role="status">
              <span class="state-section-title">警告</span>
              <p v-for="warning in currentAgentState.warnings" :key="`${warning.code}:${warning.source}`">
                {{ warning.message }}
              </p>
            </section>
            <ContextUsagePanel
              :usage="currentTokenUsage"
              :compressing="isContextCompressionPending"
              :compression-disabled="!agentExecutionAvailable || isProcessing || hasQueuedInputs || isWaitingForUserAction"
              @compress="handleContextCompression"
            />

            <section
              v-if="currentStateFiles.length"
              class="state-section"
              :class="{ 'is-collapsed': !isStateSectionExpanded('files') }"
            >
              <button
                type="button"
                class="state-section-header"
                :aria-expanded="isStateSectionExpanded('files')"
                aria-controls="state-section-files"
                @click="toggleStateSection('files')"
              >
                <span class="state-section-label">
                  <span class="state-section-title">附件/文件</span>
                  <ChevronDown
                    :size="15"
                    class="state-section-chevron"
                    :class="{ 'is-collapsed': !isStateSectionExpanded('files') }"
                  />
                </span>
                <span class="state-section-meta">{{ currentStateFiles.length }}</span>
              </button>
              <div
                class="state-collapse-panel"
                :class="{ 'is-expanded': isStateSectionExpanded('files') }"
              >
                <div class="state-collapse-inner">
                  <div id="state-section-files" class="state-section-content">
                    <div class="state-list">
                      <button
                        v-for="file in currentStateFiles"
                        :key="file.key"
                        type="button"
                        class="state-list-item state-list-item--button state-list-item--file"
                        :title="`打开 ${file.name}`"
                        @click="openPanelPreview(file)"
                      >
                        <FileTypeIcon
                          :name="file.name || file.path"
                          :size="15"
                          class="state-list-item-icon"
                        />
                        <div class="state-list-item-body">
                          <div class="state-list-item-title">{{ file.name }}</div>
                        </div>
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            </section>

            <section
              v-if="currentArtifactFiles.length"
              class="state-section"
              :class="{ 'is-collapsed': !isStateSectionExpanded('artifacts') }"
            >
              <button
                type="button"
                class="state-section-header"
                :aria-expanded="isStateSectionExpanded('artifacts')"
                aria-controls="state-section-artifacts"
                @click="toggleStateSection('artifacts')"
              >
                <span class="state-section-label">
                  <span class="state-section-title">产物</span>
                  <ChevronDown
                    :size="15"
                    class="state-section-chevron"
                    :class="{ 'is-collapsed': !isStateSectionExpanded('artifacts') }"
                  />
                </span>
                <span class="state-section-meta">{{ currentArtifactFiles.length }}</span>
              </button>
              <div
                class="state-collapse-panel"
                :class="{ 'is-expanded': isStateSectionExpanded('artifacts') }"
              >
                <div class="state-collapse-inner">
                  <div id="state-section-artifacts" class="state-section-content">
                    <div class="state-list">
                      <button
                        v-for="file in currentArtifactFiles"
                        :key="file.path"
                        type="button"
                        class="state-list-item state-list-item--button state-list-item--artifact"
                        :title="`打开 ${file.name}`"
                        @click="openPanelPreview(file)"
                      >
                        <FileTypeIcon
                          :name="file.name || file.path"
                          :size="15"
                          class="state-list-item-icon"
                        />
                        <div class="state-list-item-body">
                          <div class="state-list-item-title">{{ file.name }}</div>
                        </div>
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            </section>

            <CooperationTree
              v-if="cooperationSessions.length > 1 || cooperationError"
              compact
              :sessions="cooperationSessions"
              :current-id="currentChatId"
              :error="cooperationError"
              @open-tree="openCooperationTree"
              @refresh="refreshCooperation"
            />

            <div v-if="!hasVisibleStateSections" class="state-panel-empty">暂无状态内容</div>
          </div>
        </div>
      </div>

      <div
        v-if="!embedded"
        id="agent-file-panel"
        class="side-panel side-panel--file"
        :inert="!isFilePanelOpen"
        :aria-hidden="!isFilePanelOpen"
        ref="panelWrapperRef"
        :class="{
          'is-visible': isFilePanelOpen,
          'no-transition': isResizing
        }"
        :style="{
          width: filePanelWidthStyle
        }"
      >
        <AgentPanel
          :agent-state="currentAgentState"
          :thread-id="currentChatId"
          :active-run-id="currentThreadState?.activeRunId || null"
          :run-active="Boolean(currentThreadState?.activeRunId && currentThreadState?.isStreaming)"
          :visible="isFilePanelOpen && cooperationObservationEnabled"
          :panel-ratio="panelRatio"
          :preview-tabs="agentPanelPreviewTabs"
          :preview-cache="agentPanelPreviewCache"
          :active-preview-path="agentPanelActivePreviewPath"
          :view-mode="agentPanelViewMode"
          :maximized="isAgentPanelMaximized"
          :sections="visibleAgentPanelSections"
          :active-section-key="agentPanelActiveSectionKey"
          :filesystem-visible="agentPanelFilesystemVisible"
          :filesystem-polling-active="agentPanelFilesystemPollingActive"
          :filesystem-refresh-version="agentPanelFilesystemRefreshVersion"
          @close="closeFilePanel"
          @refresh="handleAgentStateRefresh"
          @resize="handlePanelResize"
          @resizing="handleResizingChange"
          @open-preview="openPanelPreview"
          @activate-preview="activatePanelPreview"
          @close-preview-tab="closePanelPreviewTab"
          @close-preview-path="closePanelPreviewPath"
          @view-mode-change="setAgentPanelViewMode"
          @toggle-maximize="toggleAgentPanelMaximized"
          @activate-section="activateAgentPanelSection"
          @close-section="closeAgentPanelSection"
        >
          <template #window-actions>
            <button
              v-if="showStateEntry && isAgentPanelMaximized"
              class="agent-nav-btn agent-state-btn"
              ref="panelStateTriggerRef"
              type="button"
              title="主对话状态"
              aria-label="主对话状态"
              :aria-expanded="statePanelOpen"
              @click="toggleStatePanel"
            >
              <Gauge :size="16" />
            </button>
          </template>
          <template #session-content>
            <CooperationTree
              v-if="agentPanelSections.some((section) => section.key === 'cooperation')"
              v-show="agentPanelActiveSectionKey === 'cooperation'"
              :sessions="cooperationSessions"
              :current-id="currentChatId"
              :error="cooperationError"
              @open="openCooperationSession"
              @refresh="refreshCooperation"
            />
            <div
              :id="mainChatPanelId"
              v-show="isAgentPanelMaximized && agentPanelActiveSectionKey === 'main-session'"
              class="panel-main-chat"
            ></div>
            <div
              v-for="section in agentPanelSections.filter((item) => item.type === 'session')"
              :key="section.key"
              v-show="agentPanelActiveSectionKey === section.key"
              class="panel-session-chat"
            >
              <SessionWorkspace
                :ref="(workspace) => setCooperationWorkspaceRef(section.sessionId, workspace)"
                :embedded="true"
                :read-only="effectivePersonalMode"
                :send-disabled="sendDisabled"
                :visible="
                  workspaceActive && isFilePanelOpen && agentPanelActiveSectionKey === section.key
                "
                @open-session="navigateToSession"
                :cooperation-members="cooperationSessions"
                :embedded-thread-id="section.sessionId"
                :agent-id="effectivePersonalMode ? '' : currentAgentId"
                :is-new-session="false"
              />
            </div>
          </template>
        </AgentPanel>
      </div>
    </div>
  </div>
</template>

<script setup>
import { useSessionAgentAvailability } from '@/modules/session/model/sessionAgentAvailability'
import {
  ref,
  reactive,
  onMounted,
  watch,
  nextTick,
  computed,
  provide,
  onUnmounted,
  onActivated,
  onDeactivated,
  getCurrentInstance
} from 'vue'
import { message } from 'ant-design-vue'
import { useRouter } from 'vue-router'
import { createSessionLinkNavigator } from '@/modules/session/model/sessionLinks'
import {
  ArrowUpFromLine,
  ArrowUpRight,
  MessageSquarePlus,
  Bug,
  ChevronDown,
  CornerDownRight,
  PanelRight,
  Gauge,
  Play,
  RefreshCw,
  Trash2,
  X
} from '@lucide/vue'
import FileTypeIcon from '@/shared/ui/FileTypeIcon.vue'
import AgentInputArea from '@/modules/session/ui/AgentInputArea.vue'
import ContextUsagePanel from './ContextUsagePanel.vue'
import CooperationAttention from '@/modules/session/ui/CooperationAttention.vue'
import {
  getCooperationPendingActions,
  getSessionAttentionLabel
} from '@/modules/session/model/cooperationAttention'
import ContextUsageRing from '@/modules/session/ui/ContextUsageRing.vue'
import ToolApprovalModeSelector from '@/modules/session/ui/ToolApprovalModeSelector.vue'
import ModelSelectorComponent from '@/modules/agents/ui/ModelSelectorComponent.vue'
import AgentMessageComponent from '@/modules/session/ui/AgentMessageComponent.vue'
import RunFailureNotice from '@/modules/session/ui/RunFailureNotice.vue'
import {
  formatEmptyRunStatus,
  groupRunContinuations,
  isRunGroupSettled as isRunGroupExecutionSettled
} from '@/modules/session/model/runProcessGrouping'
import RefsComponent from '@/modules/session/ui/RefsComponent.vue'
import ToolCallsGroupComponent from '@/modules/session/ui/ToolCallsGroupComponent.vue'
import RunProcessGroupComponent from '@/modules/session/ui/RunProcessGroupComponent.vue'
import { handleChatError, handleValidationError } from '@/shared/lib/errorHandler'
import {
  DRAFT_THREAD_ID,
  threadDraftStore,
  createThreadDraftSession,
  entryDraftHandoff
} from '@/modules/session/model/thread_draft'
import { ScrollController } from '@/modules/session/model/scrollController'
import { resolveContextUsageSummary } from '@/modules/session/model/contextUsage'
import { AgentValidator } from '@/modules/agents'
import { useAgentStore } from '@/modules/agents'
import { useChatThreadsStore } from '@/modules/session/model/chatThreads'
import { normalizeAgentConfigurableItems } from '@/modules/agents'
import { useConfigStore } from '@/modules/settings'
import { useInfoStore } from '@/modules/settings'
import { useUserStore } from '@/modules/identity'
import { storeToRefs } from 'pinia'
import { getMessageRunId } from '@/modules/session/model/messageDebug'
import { MessageProcessor } from '@/modules/session/model/messageProcessor'
import { itemsToMessages } from '@/modules/session/model/agentItems'
import dayjs, { parseToShanghai } from '@/shared/lib/time'
import { agentApi, threadApi } from '@/apis'
import HumanApprovalModal from '@/modules/session/ui/HumanApprovalModal.vue'
import { useApproval } from '@/modules/session/model/useApproval'
import { IDLE_QUEUE_SNAPSHOT } from '@/modules/session/model/useAgentThreadState'
import { createSessionInput, createInputRequestId } from '@/modules/session/model/sessionInput'
import { useSessionRuntimeStore } from '@/modules/session/model/sessionRuntime'
import { resumeSessionWaitpoint } from '@/modules/session/model/sessionCommands'
import { useSessionCooperation } from '@/modules/session/model/useSessionCooperation'
import CooperationTree from '@/modules/session/ui/CooperationTree.vue'
import { personalMessages } from '@/modules/session/model/personalMessages'
import { useAgentMentionConfig } from '@/modules/session/model/useAgentMentionConfig'
import { getToolCallId, parseToolCallResult } from '@/modules/session/model/toolCallProjection'
import { collectCooperationTasks } from '@/modules/session/model/cooperationToolView'
import AgentArtifactsCard from '@/modules/session/ui/AgentArtifactsCard.vue'
import { normalizeArtifacts } from '@/modules/session/model/artifacts'
import AgentPanel from '@/modules/session/ui/workspace/AgentPanel.vue'
import AttachmentTmpUploadModal from '@/modules/session/ui/AttachmentTmpUploadModal.vue'
import ProjectSelectionSection from '@/modules/projects/ui/ProjectSelectionSection.vue'
import { normalizeToolCalls } from '@/modules/session/ui/tools/toolRegistry'
import { getMessageGroupDisplayItems } from '@/modules/session/model/messageGrouping'
import { getStatePanelPlacement } from '@/modules/session/model/statePanelLayout'
import { useOutsidePointerdown } from '@/shared/lib/useOutsidePointerdown'
import { AUTO_PROJECT_ID } from '@/modules/projects'
import { createSingleFlight } from '@/shared/lib/singleFlight'
import { createThreadForContext } from '@/modules/session/model/threadCreation'
import {
  FILE_TREE_SECTION,
  MESSAGE_DEBUG_SECTION,
  TURN_SOURCES_SECTION,
  closeAgentPanelSection as closePanelSectionState,
  shouldPollAgentPanelFilesystem,
  upsertAgentPanelSection
} from '@/modules/session/model/agentPanelSections'
import {
  isRunInterruptedConflict,
  isThreadWaitingForUserAction,
  isToolApprovalMode,
  readToolApprovalModePreference,
  resolveToolApprovalMode,
  writeToolApprovalModePreference
} from '@/modules/session/model/toolApproval'

// ==================== PROPS & EMITS ====================
const props = defineProps({
  embedded: { type: Boolean, default: false },
  visible: { type: Boolean, default: true },
  embeddedThreadId: { type: String, default: '' },
  cooperationMembers: { type: Array, default: () => [] },
  agentId: { type: String, default: '' },
  initialProjectId: { type: String, default: '' },
  isNewSession: { type: Boolean, required: true },
  sendDisabled: { type: Boolean, default: false },
  personalMode: { type: Boolean, default: false },
  personalProfileOpen: { type: Boolean, default: false },
  readOnly: { type: Boolean, default: false }
})
const emit = defineEmits(['thread-change', 'open-session', 'status-change'])
const router = useRouter()

// ==================== STORE MANAGEMENT ====================
const agentStore = useAgentStore()
const chatThreadsStore = useChatThreadsStore()
const configStore = useConfigStore()
const infoStore = useInfoStore()
const userStore = useUserStore()
const messageDebugEnabled = computed(() => infoStore.debugMode && userStore.isSuperAdmin)
const { agents, selectedAgentId, agentDetails, availableKnowledgeBases } = storeToRefs(agentStore)
const threadStoreRefs = storeToRefs(chatThreadsStore)
const { threads, threadCreationInFlight } = threadStoreRefs
const currentThreadId = ref(null)
const showStartScreen = computed(
  () => props.isNewSession && !currentThreadId.value && !threadCreationInFlight.value
)
const currentThread = ref(null)
// 列表翻页不能丢弃保活实例的会话详情；新详情仍从共享目录同步。
watch(
  () => threads.value.find((thread) => thread.id === currentThreadId.value) || null,
  (thread) => {
    if (thread || currentThread.value?.id !== currentThreadId.value) currentThread.value = thread
  }
)
const workspacePanelId = `session-workspace-${getCurrentInstance().uid}`
const mainChatPanelId = `main-chat-panel-${getCurrentInstance().uid}`
const isLoadingMessages = ref(false)
const workspaceActivated = ref(true)
const workspaceActive = computed(() => workspaceActivated.value && props.visible)

// ==================== LOCAL CHAT & UI STATE ====================
// 输入草稿按线程保存：初始按当前线程还原，后续输入实时写入对应线程
const threadDraftSession = createThreadDraftSession(threadDraftStore, currentThreadId.value)
const userInput = ref(threadDraftStore.read(currentThreadId.value || DRAFT_THREAD_ID))
watch(userInput, (text) => threadDraftSession.saveInput(text))
const agentInputAreaRef = ref(null)

/** 组件动作追加到当前草稿，保留用户已输入内容并等待显式发送。 */
function composeWidgetDraft(text) {
  userInput.value = [userInput.value, text].filter(Boolean).join('\n\n')
  nextTick(() => agentInputAreaRef.value?.focus())
}

/** 表单和按钮独立发送用户回答，旧列表动作继续填写草稿。 */
function handleWidgetAction(action) {
  if (props.readOnly) return false
  if (typeof action === 'string') return composeWidgetDraft(action)
  if (action?.type !== 'send_message' || widgetActionsDisabled.value) return false
  return handleSendMessage({
    text: action.text,
    fromWidget: true,
    onWidgetAccepted: action.onAccepted
  })
}

watch(
  [workspaceActive, () => props.isNewSession, currentThreadId],
  ([active, isNewSession, threadId]) => {
    if (!active || !isNewSession || threadId || props.embedded) return
    const text = entryDraftHandoff.take()
    if (text) composeWidgetDraft(text)
  },
  { immediate: true, flush: 'post' }
)

const messageInputStageRef = ref(null)
const cooperationWorkspaceRefs = new Map()
let workspaceReady = Promise.resolve()
const pendingActionFocusTurnId = ref(null)
const sendCooldownActive = ref(false)
const cancellingInputIds = reactive(new Set())
const promotingInputIds = reactive(new Set())
let sendCooldownTimer = null
// 预设的打招呼文本
const greetingMessages = [
  '语析，Natural Language Processing',
  '语析，与知识对话',
  '答案藏在知识里，我来找',
  '与知识对话，与答案相遇',
  '你负责提问，我负责寻找'
]

// 随机选择一个打招呼文本
const randomGreeting = greetingMessages[Math.floor(Math.random() * greetingMessages.length)]

const sessionRuntime = useSessionRuntimeStore()
const {
  getThreadState,
  resetThreadExecution,
  fetchThreadMessages,
  fetchAgentState,
  startRunStream,
  requestTurnCancellation,
  resumeActiveRunForThread,
  resumeQueuedInputs,
  cancelInput,
  promoteInput,
  continueQueue
} = sessionRuntime
const { threadMessages, threadRuns, historyPages, pendingSends } = storeToRefs(sessionRuntime)
const setCurrentThreadId = (threadId, options) => {
  currentThreadId.value = threadId || null
  if (!props.embedded && !effectivePersonalMode.value && workspaceActive.value) {
    chatThreadsStore.setCurrentThreadId(threadId || null, options)
  }
}
const threadAttachmentsMap = ref({})
const draftFilesByThread = ref({})
const attachmentUploadModalOpen = ref(false)
const attachmentUploadThreadKey = ref(null)
const attachmentInitialFiles = ref([])
const attachmentInitialFilesKey = ref(0)
const selectedProjectId = ref(AUTO_PROJECT_ID)
const threadCreationRequest = ref(null)
const isRefreshingState = ref(false)
const approvalSubmitting = computed(() =>
  Boolean(getThreadState(currentThreadId.value)?.approvalSubmitting)
)
const collapsedStateSections = reactive({
  files: false,
  artifacts: false
})
const threadConfigNoticeMap = ref({})
const threadPendingConfigNoticeMap = ref({})
const threadConfigSnapshotMap = ref({})
const configNoticeSyncDepth = ref(0)
const configNoticeScrollVersion = ref(0)

// 本地 UI 状态（仅在本组件使用）
const localUIState = reactive({
  chatMainWidth: typeof window !== 'undefined' ? window.innerWidth : 0,
  chatMainVisibleWidth: 0,
  chatContentWidth: typeof window !== 'undefined' ? window.innerWidth : 0
})

// Agent Panel State
const isFilePanelOpen = ref(false)
const statePanelOpen = ref(false)
const workspaceChatRef = ref(null)
const mainStateTriggerRef = ref(null)
const panelStateTriggerRef = ref(null)
const statePanelRef = ref(null)
const statePanelPlacement = ref({ mode: 'floating', style: {} })

useOutsidePointerdown(
  computed({
    get: () =>
      workspaceActive.value &&
      statePanelOpen.value &&
      statePanelPlacement.value.mode === 'floating',
    set: (open) => {
      statePanelOpen.value = open
    }
  }),
  [statePanelRef, mainStateTriggerRef, panelStateTriggerRef]
)

/** 状态面板靠右显示，保留顶部工具栏和输入区的操作空间。 */
const updateStatePanelPlacement = () => {
  if (!statePanelOpen.value || !stateTriggerRef.value || !workspaceChatRef.value) return
  const trigger = stateTriggerRef.value.getBoundingClientRect()
  if (!trigger.width || !trigger.height) return
  const workspace = workspaceChatRef.value.getBoundingClientRect()
  const visibleInput = [...workspaceChatRef.value.querySelectorAll('.bottom')]
    .map((element) => element.getBoundingClientRect())
    .find((rect) => rect.height > 0 && rect.top > trigger.bottom)
  const main = chatMainRef.value?.getBoundingClientRect()
  const embeddedArea =
    !isAgentPanelMaximized.value && main && main.width >= 1000
      ? {
          left: Math.min(main.right, workspace.right) - 348,
          right: Math.min(main.right, workspace.right) - 8
        }
      : null
  statePanelPlacement.value = getStatePanelPlacement(
    workspace,
    trigger,
    visibleInput,
    embeddedArea,
    !isAgentPanelMaximized.value && main ? main : workspace
  )
}

const sideActive = computed(() => {
  if (isFilePanelOpen.value) return 'file'
  if (statePanelOpen.value) return 'state'
  return ''
})
const isResizing = ref(false)
const isAgentPanelMaximized = ref(false)
const stateTriggerRef = computed(() =>
  isAgentPanelMaximized.value ? panelStateTriggerRef.value : mainStateTriggerRef.value
)
const defaultPanelRatio = 0.5
const previewPanelRatio = 0.5
const minPanelRatio = 0.25
const maxPanelRatio = 0.75
const minChatMainWidth = 350
const filePanelGapWidth = 0
const mobilePanelBreakpoint = 768
const panelRatio = ref(defaultPanelRatio) // 面板宽度比例 (0-1)
const filePanelDragWidth = ref(null)
const agentPanelPreviewTabs = ref([])
const agentPanelPreviewCache = reactive(new Map())
const agentPanelActivePreviewPath = ref('')
const agentPanelViewMode = ref('tree')
const agentPanelSections = ref([])
const agentPanelActiveSectionKey = ref('')
watch(
  [
    statePanelOpen,
    stateTriggerRef,
    isAgentPanelMaximized,
    isFilePanelOpen,
    panelRatio,
    agentPanelActiveSectionKey
  ],
  async () => {
    await nextTick()
    updateStatePanelPlacement()
  },
  { flush: 'post' }
)
const agentPanelFilesystemRefreshVersion = ref(0)
const pageVisible = ref(typeof document === 'undefined' || document.visibilityState === 'visible')
const chatContentContainerRef = ref(null)
const panelWrapperRef = ref(null) // 直接操作 DOM
let resizeStartX = 0
let resizeStartWidth = 0
let panelContainerWidth = 0
let streamingStateRefreshTimer = null

const getPanelContainerWidth = () => {
  const container = chatContentContainerRef.value || panelWrapperRef.value?.parentElement
  return container?.clientWidth || (typeof window !== 'undefined' ? window.innerWidth : 0)
}

const getFilePanelMaxWidth = (containerWidth = getPanelContainerWidth()) => {
  if (!containerWidth) return 0
  if (containerWidth <= mobilePanelBreakpoint) return Math.max(0, containerWidth - 16)
  return Math.max(0, containerWidth - minChatMainWidth - filePanelGapWidth)
}

const getFilePanelMinWidth = (containerWidth, maxWidth = getFilePanelMaxWidth(containerWidth)) => {
  const preferredMinWidth = containerWidth <= mobilePanelBreakpoint ? 280 : 320
  return Math.min(preferredMinWidth, maxWidth)
}

const getMaxPanelRatio = (containerWidth = getPanelContainerWidth()) => {
  if (!containerWidth) return maxPanelRatio
  return Math.max(
    minPanelRatio,
    Math.min(maxPanelRatio, getFilePanelMaxWidth(containerWidth) / containerWidth)
  )
}

const clampPanelRatio = (ratio, containerWidth = getPanelContainerWidth()) => {
  return Math.max(minPanelRatio, Math.min(ratio, getMaxPanelRatio(containerWidth)))
}

const filePanelWidthStyle = computed(() => {
  if (!isFilePanelOpen.value) return '0px'
  if (isAgentPanelMaximized.value) {
    const containerWidth = localUIState.chatContentWidth || getPanelContainerWidth()
    return containerWidth ? `${containerWidth}px` : '100%'
  }
  if (filePanelDragWidth.value !== null) return `${filePanelDragWidth.value}px`

  const containerWidth = localUIState.chatContentWidth || getPanelContainerWidth()
  if (!containerWidth) return `${panelRatio.value * 100}%`

  const maxWidth = getFilePanelMaxWidth(containerWidth)
  const minWidth = getFilePanelMinWidth(containerWidth, maxWidth)
  const preferredWidth = containerWidth * panelRatio.value
  return `${Math.max(minWidth, Math.min(preferredWidth, maxWidth))}px`
})

const setPanelRatioForViewMode = () => {
  const hasPreview = Boolean(agentPanelActivePreviewPath.value)
  panelRatio.value = clampPanelRatio(hasPreview ? previewPanelRatio : defaultPanelRatio)
}

const showFilePanel = (mode = 'tree') => {
  isFilePanelOpen.value = true
  statePanelOpen.value = false
  agentPanelViewMode.value =
    mode === 'preview' && agentPanelActivePreviewPath.value ? 'preview' : 'tree'
  setPanelRatioForViewMode()
}

const showFileTreePanel = () => {
  isFilePanelOpen.value = true
  statePanelOpen.value = false
  agentPanelActiveSectionKey.value = FILE_TREE_SECTION.key
  agentPanelActivePreviewPath.value = ''
  agentPanelViewMode.value = 'tree'
  setPanelRatioForViewMode()
}

const getPanelFileName = (file) => {
  if (file?.name) return file.name
  if (file?.path) return String(file.path).split('/').pop() || String(file.path)
  return '未知文件'
}

const normalizePanelPath = (path) => String(path || '').replace(/\/+$/, '')

const isSameOrChildPanelPath = (path, targetPath) => {
  const normalizedPath = normalizePanelPath(path)
  const normalizedTargetPath = normalizePanelPath(targetPath)
  if (!normalizedPath || !normalizedTargetPath) return false
  return (
    normalizedPath === normalizedTargetPath || normalizedPath.startsWith(`${normalizedTargetPath}/`)
  )
}

const resetAgentPanelState = () => {
  isFilePanelOpen.value = false
  statePanelOpen.value = false
  panelRatio.value = defaultPanelRatio
  isAgentPanelMaximized.value = false
  agentPanelPreviewTabs.value = []
  agentPanelActivePreviewPath.value = ''
  agentPanelViewMode.value = 'tree'
  agentPanelSections.value = effectivePersonalMode.value ? [] : [FILE_TREE_SECTION]
  agentPanelActiveSectionKey.value = agentPanelSections.value[0]?.key || ''
}

const previewCacheKey = (path, threadId = currentChatId.value) => `${threadId}:${path}`

// 用户目录文件使用独立 cache 前缀；释放时两个 scope 的 key 一并清理。
const releasePreviewCacheEntry = (path, threadId = currentChatId.value) => {
  for (const key of [previewCacheKey(path, threadId), `workspace:${path}`]) {
    const entry = agentPanelPreviewCache.get(key)
    if (entry?.file?.previewUrl) window.URL.revokeObjectURL(entry.file.previewUrl)
    agentPanelPreviewCache.delete(key)
  }
}

const invalidatePreviewCachePath = (targetPath, threadId = currentChatId.value) => {
  for (const key of agentPanelPreviewCache.keys()) {
    const separatorIndex = key.indexOf(':')
    if (separatorIndex < 0 || key.slice(0, separatorIndex) !== String(threadId)) continue
    const path = key.slice(separatorIndex + 1)
    if (isSameOrChildPanelPath(path, targetPath)) releasePreviewCacheEntry(path, threadId)
  }
}

const setAgentPanelViewMode = (mode) => {
  agentPanelViewMode.value =
    mode === 'preview' && agentPanelActivePreviewPath.value ? 'preview' : 'tree'
  setPanelRatioForViewMode()
}

const activatePanelPreview = (path) => {
  if (!path) return
  agentPanelActiveSectionKey.value = `file:${path}`
  agentPanelActivePreviewPath.value = path
  showFilePanel('preview')
}

const openPanelPreview = (file, keepTreeOpen = false) => {
  if (!file?.path) return

  const workspace = file.workspace === true
  const tab = {
    ...file,
    workspace,
    workdir: !workspace && file.workdir === true,
    path: String(file.path),
    name: getPanelFileName(file)
  }
  const existingIndex = agentPanelPreviewTabs.value.findIndex((item) => item.path === tab.path)

  if (existingIndex >= 0) {
    const existingTab = agentPanelPreviewTabs.value[existingIndex]
    if (
      (!tab.workdir && !tab.workspace && tab.modified_at == null && tab.size == null) ||
      existingTab.modified_at !== tab.modified_at ||
      existingTab.size !== tab.size ||
      Boolean(existingTab.workdir) !== Boolean(tab.workdir) ||
      Boolean(existingTab.workspace) !== Boolean(tab.workspace)
    ) {
      releasePreviewCacheEntry(tab.path)
    }
    agentPanelPreviewTabs.value = agentPanelPreviewTabs.value.map((item, index) =>
      index === existingIndex ? { ...item, ...tab } : item
    )
  } else {
    agentPanelPreviewTabs.value = [...agentPanelPreviewTabs.value, tab]
  }

  agentPanelActivePreviewPath.value = tab.path
  agentPanelSections.value = upsertAgentPanelSection(agentPanelSections.value, {
    key: `file:${tab.path}`,
    type: 'file',
    title: tab.name,
    path: tab.path
  })
  agentPanelActiveSectionKey.value = keepTreeOpen && !effectivePersonalMode.value ? FILE_TREE_SECTION.key : `file:${tab.path}`
  showFilePanel('preview')
}

const closePanelPreviewTab = (path) => {
  if (!path) return

  releasePreviewCacheEntry(path)

  const nextTabs = agentPanelPreviewTabs.value.filter((item) => item.path !== path)
  agentPanelPreviewTabs.value = nextTabs
  const nextSectionState = closePanelSectionState(
    agentPanelSections.value,
    agentPanelActiveSectionKey.value,
    `file:${path}`
  )
  agentPanelSections.value = nextSectionState.sections
  agentPanelActiveSectionKey.value = nextSectionState.activeKey
  if (!nextSectionState.sections.length) closeFilePanel()

  if (agentPanelActivePreviewPath.value !== path) return

  const activeSection = agentPanelSections.value.find(
    (section) => section.key === agentPanelActiveSectionKey.value
  )
  agentPanelActivePreviewPath.value = activeSection?.type === 'file' ? activeSection.path : ''
  agentPanelViewMode.value = activeSection?.type === 'file' ? 'preview' : 'tree'
  setPanelRatioForViewMode()
}

const closePanelPreviewPath = (targetPath) => {
  if (!targetPath) return

  invalidatePreviewCachePath(targetPath)

  const nextTabs = agentPanelPreviewTabs.value.filter(
    (item) => !isSameOrChildPanelPath(item.path, targetPath)
  )
  const shouldCloseActive = isSameOrChildPanelPath(agentPanelActivePreviewPath.value, targetPath)
  agentPanelPreviewTabs.value = nextTabs
  const removedPaths = agentPanelSections.value
    .filter(
      (section) => section.type === 'file' && isSameOrChildPanelPath(section.path, targetPath)
    )
    .map((section) => section.key)
  agentPanelSections.value = agentPanelSections.value.filter(
    (section) => !removedPaths.includes(section.key)
  )
  if (removedPaths.includes(agentPanelActiveSectionKey.value)) {
    agentPanelActiveSectionKey.value = agentPanelSections.value[0]?.key || ''
  }
  if (!agentPanelSections.value.length) closeFilePanel()

  if (!shouldCloseActive) return

  const activeSection = agentPanelSections.value.find(
    (section) => section.key === agentPanelActiveSectionKey.value
  )
  agentPanelActivePreviewPath.value = activeSection?.type === 'file' ? activeSection.path : ''
  agentPanelViewMode.value = activeSection?.type === 'file' ? 'preview' : 'tree'
  setPanelRatioForViewMode()
}

// ==================== COMPUTED PROPERTIES ====================
const currentAgentId = computed(() => {
  return currentThread.value?.agent.id || props.agentId || selectedAgentId.value
})

const { status: sessionAgentStatus, refresh: refreshSessionAgent } = useSessionAgentAvailability(
  currentThreadId, (agentId) => agentStore.fetchAgentDetail(agentId, true, { notify: false })
)
const agentExecutionAvailable = computed(() =>
  currentChatId.value ? sessionAgentStatus.value === 'ready' : Boolean(currentAgent.value?.can_run !== false && currentAgent.value)
)
const isSessionLoading = computed(() =>
  (props.isNewSession && threadCreationInFlight.value) ||
  (!currentThreadState.value?.historyLoaded && (
    isLoadingMessages.value ||
    (Boolean(currentChatId.value) && sessionAgentStatus.value === 'loading')
  ))
)

/** 重用对话加载入口重试，临时错误不改变对话身份。 */
const retrySessionAgent = async () => {
  if (currentChatId.value) await selectChat(currentChatId.value)
}

const currentAgentName = computed(() => {
  const agent = currentAgent.value
  return agent ? agent.name : '智能体'
})

const currentAgent = computed(() => {
  if (!currentAgentId.value) return null
  return (
    agentDetails.value[currentAgentId.value] ||
    (agents.value || []).find((agent) => agent.agent_id === currentAgentId.value) ||
    null
  )
})
// 直接打开个人会话时同样使用受限的个人界面。
const effectivePersonalMode = computed(() => !props.readOnly && (props.personalMode || currentAgent.value?.is_personal_assistant === true))
const personalProfileDockRef = ref(null)
const personalActionsRef = ref(null)
// 使用主对话的未扣除卡片宽度，避免预留空间后反复触发平铺/悬浮切换。
const personalProfileEmbedded = computed(() =>
  effectivePersonalMode.value && props.personalProfileOpen &&
  !isAgentPanelMaximized.value && localUIState.chatMainVisibleWidth >= 1100
)
watch(effectivePersonalMode, (personal) => {
  agentPanelSections.value = personal
    ? agentPanelSections.value.filter(section => section.type !== 'file-tree')
    : upsertAgentPanelSection(agentPanelSections.value, FILE_TREE_SECTION)
  if (!agentPanelSections.value.some(section => section.key === agentPanelActiveSectionKey.value)) {
    agentPanelActiveSectionKey.value = agentPanelSections.value[0]?.key || ''
  }
}, { immediate: true })
const agentConfig = computed(() => {
  if (!currentThread.value) return agentStore.agentConfig
  const config = currentAgent.value?.config_json || {}
  return config.context || config
})
const configurableItems = computed(() =>
  currentThread.value
    ? normalizeAgentConfigurableItems(currentAgent.value?.configurable_items)
    : agentStore.configurableItems
)
const currentChatId = computed(() => currentThreadId.value)

watch(
  [currentChatId, () => props.initialProjectId],
  ([threadId, initialProjectId]) => {
    if (!threadId) selectedProjectId.value = initialProjectId || AUTO_PROJECT_ID
  },
  { immediate: true }
)

// ==================== 对话级模型覆盖 ====================
// 当前选择优先；否则依次使用 Session、智能体和系统默认模型。
const DRAFT_MODEL_KEY = '__draft__'
const selectedModelByThread = reactive({})
const savedToolApprovalMode = ref(readToolApprovalModePreference())
const agentDefaultModel = computed(
  () =>
    agentConfig.value?.model ||
    currentAgent.value?.config_json?.context?.model ||
    configStore.config?.default_model ||
    ''
)
const currentModelSpec = computed(
  () =>
    selectedModelByThread[currentChatId.value || DRAFT_MODEL_KEY] ||
    currentThread.value?.agent.model ||
    agentDefaultModel.value
)
const handleModelSelect = (spec) => {
  if (typeof spec === 'string') {
    if (spec) {
      selectedModelByThread[currentChatId.value || DRAFT_MODEL_KEY] = spec
    } else {
      delete selectedModelByThread[currentChatId.value || DRAFT_MODEL_KEY]
    }
  }
}

const configuredAgentToolApprovalMode = computed(() => {
  const configJson = currentAgent.value?.config_json
  return configJson?.context?.tool_approval_mode || configJson?.tool_approval_mode || null
})
const currentToolApprovalMode = computed(() =>
  resolveToolApprovalMode({
    hasThread: Boolean(currentChatId.value),
    threadMode: currentThread.value?.yuxi.tool_approval_mode,
    agentMode: configuredAgentToolApprovalMode.value,
    savedMode: savedToolApprovalMode.value
  })
)
const handleToolApprovalModeSelect = async (mode) => {
  if (!isToolApprovalMode(mode)) return

  const thread = currentThread.value
  if (!thread) {
    savedToolApprovalMode.value = mode
    writeToolApprovalModePreference(mode)
    return
  }

  const previousMode = thread.yuxi.tool_approval_mode
  thread.yuxi.tool_approval_mode = mode
  try {
    await chatThreadsStore.updateThread(thread.id, null, undefined, mode)
    savedToolApprovalMode.value = mode
    writeToolApprovalModePreference(mode)
  } catch {
    thread.yuxi.tool_approval_mode = previousMode
    message.error('审批模式保存失败')
  }
}

const currentThreadAgentName = computed(() => {
  const threadAgentId = currentThread.value?.agent.id
  if (threadAgentId && agents.value?.length) {
    const threadAgent = agents.value.find((agent) => agent.agent_id === threadAgentId)
    if (threadAgent?.name) {
      return threadAgent.name
    }
  }
  return currentAgentName.value
})
// AgentState 相关计算属性
const currentAgentState = computed(() => {
  return currentChatId.value ? getThreadState(currentChatId.value)?.agentState || null : null
})
const currentTokenUsage = computed(() => {
  const usage = currentAgentState.value?.token_usage
  return usage && typeof usage === 'object' && !Array.isArray(usage) ? usage : null
})
const contextUsageSummary = computed(() => resolveContextUsageSummary(currentTokenUsage.value))
const isContextCompressionPending = computed(() =>
  Boolean(currentThreadState.value?.contextCompressing)
)
const currentThreadAttachments = computed(() => {
  if (!currentChatId.value) return []
  return threadAttachmentsMap.value[currentChatId.value] || []
})
const currentPendingThreadAttachments = computed(
  () => draftFilesByThread.value[currentChatId.value || DRAFT_MODEL_KEY] || []
)
const currentArtifacts = computed(() => {
  const artifacts = currentAgentState.value?.artifacts
  return Array.isArray(artifacts) ? artifacts : []
})
const currentArtifactFiles = computed(() =>
  normalizeArtifacts(currentArtifacts.value).map((path) => ({
    path,
    name: getPanelFileName({ path })
  }))
)
const cooperationObservationEnabled = computed(
  () => !props.embedded && workspaceActive.value && pageVisible.value
)
const {
  sessions: cooperationSessions,
  error: cooperationError,
  refresh: refreshCooperation
} = useSessionCooperation({
  threadId: computed(() => (userStore.isLoggedIn ? currentChatId.value : '')),
  enabled: cooperationObservationEnabled,
  onSnapshot: sessionRuntime.reconcileCooperationSessions,
  loadSummary: (id) => effectivePersonalMode.value ? agentApi.getPersonalSessions() : agentApi.getCooperationSummary(id)
})

const toggleMessageDebugPanel = () => {
  if (isFilePanelOpen.value && agentPanelActiveSectionKey.value === MESSAGE_DEBUG_SECTION.key) {
    closeFilePanel()
    return
  }
  agentPanelSections.value = upsertAgentPanelSection(
    agentPanelSections.value,
    MESSAGE_DEBUG_SECTION
  )
  agentPanelActiveSectionKey.value = MESSAGE_DEBUG_SECTION.key
  isFilePanelOpen.value = true
  statePanelOpen.value = false
}

/** 消息内"来源"改为在右侧栏展开；再次点击同一 Turn 的入口时收起面板。 */
const openTurnSourcesPanel = (payload) => {
  const turnId = payload?.turnId || ''
  if (
    isFilePanelOpen.value &&
    agentPanelActiveSectionKey.value === TURN_SOURCES_SECTION.key &&
    agentPanelSections.value.find((section) => section.key === TURN_SOURCES_SECTION.key)
      ?.turnId === turnId
  ) {
    closeFilePanel()
    return
  }
  agentPanelSections.value = upsertAgentPanelSection(agentPanelSections.value, {
    ...TURN_SOURCES_SECTION,
    turnId,
    sources: {
      knowledgeChunks: Array.isArray(payload?.knowledgeChunks) ? payload.knowledgeChunks : [],
      webSources: Array.isArray(payload?.webSources) ? payload.webSources : []
    }
  })
  agentPanelActiveSectionKey.value = TURN_SOURCES_SECTION.key
  isFilePanelOpen.value = true
  statePanelOpen.value = false
}
const cooperationPendingActions = computed(() =>
  getCooperationPendingActions(cooperationSessions.value, currentChatId.value)
)
const visibleAgentPanelSections = computed(() => {
  const sessions = new Map(
    cooperationSessions.value.map((session) => [session.session_id, session])
  )
  const sections = agentPanelSections.value.map((section) => ({
    ...section,
    attentionLabel:
      section.type === 'session' ? getSessionAttentionLabel(sessions.get(section.sessionId)) : ''
  }))
  return isAgentPanelMaximized.value
    ? [
        {
          key: 'main-session',
          type: 'main-session',
          title: '当前对话',
          avatarSeed: currentChatId.value
        },
        ...sections
      ]
    : sections
})

/** 保存成员视图引用，关闭标签时释放引用。 */
function setCooperationWorkspaceRef(sessionId, workspace) {
  if (workspace) cooperationWorkspaceRefs.set(sessionId, workspace)
  else cooperationWorkspaceRefs.delete(sessionId)
}

/** 待办入口复用标签，并在成员读取当前等待点后定位人工卡片。 */
async function openCooperationAttention(sessionId) {
  openCooperationSession(sessionId)
  await nextTick()
  try {
    await cooperationWorkspaceRefs.get(sessionId)?.focusPendingAction()
  } catch (error) {
    handleChatError(error, 'load')
  } finally {
    void refreshCooperation()
  }
}

/** 在现有侧栏标签中打开协作列表，重复点击复用同一标签。 */
const openCooperationTree = () => {
  agentPanelSections.value = upsertAgentPanelSection(agentPanelSections.value, {
    key: 'cooperation',
    type: 'cooperation',
    title: '协作会话'
  })
  agentPanelActiveSectionKey.value = 'cooperation'
  isFilePanelOpen.value = true
  statePanelOpen.value = false
}

const sessionLinkNavigator = createSessionLinkNavigator({
  router,
  resolveTarget: (target) => agentApi.getSessionNavigation(target),
  isActive: () => workspaceActive.value,
  openTarget(destination) {
    if (destination.tree_root_session_id === currentChatId.value) {
      if (destination.parent_session_id) openCooperationSession(destination.session_id, destination.name)
      else if (isAgentPanelMaximized.value) agentPanelActiveSectionKey.value = 'main-session'
      else isFilePanelOpen.value = false
    }
  },
  onError: (error) => handleChatError(error, 'load')
})
watch([workspaceActive, currentChatId, () => router.currentRoute.value], () => sessionLinkNavigator.invalidate(), { flush: 'sync' })
onUnmounted(() => sessionLinkNavigator.invalidate())

/** 嵌入会话把导航交给根工作区，避免侧栏递归嵌套。 */
async function navigateToSession(target) {
  if (effectivePersonalMode.value) {
    try {
      const destination = await agentApi.getSessionNavigation(typeof target === 'string' ? target : target.session_id)
      openCooperationSession(destination.session_id, destination.name)
      await router.replace({ query: { ...router.currentRoute.value.query, session_id: destination.session_id } })
    } catch (error) {
      handleChatError(error, 'load')
    }
    return
  }
  if (props.embedded) emit('open-session', target)
  else await sessionLinkNavigator.navigate(target)
}

/** 路由恢复先校验成员属于当前根页面，再打开侧栏。 */
async function openSessionFromRoute(sessionId) {
  if (!sessionId) {
    if (isAgentPanelMaximized.value) agentPanelActiveSectionKey.value = 'main-session'
    else if (agentPanelSections.value.find((section) => section.key === agentPanelActiveSectionKey.value)?.type === 'session') {
      isFilePanelOpen.value = false
    }
    return
  }
  const target = await agentApi.getSessionNavigation(sessionId)
  if (!effectivePersonalMode.value && (target.tree_root_session_id !== currentChatId.value || !target.parent_session_id)) {
    throw new Error('会话不属于当前主页面')
  }
  openCooperationSession(target.session_id, target.name)
}

const openCooperationSession = (sessionId, title) => {
  if (!effectivePersonalMode.value && sessionId === currentChatId.value) {
    if (isAgentPanelMaximized.value) agentPanelActiveSectionKey.value = 'main-session'
    return
  }
  const session = cooperationSessions.value.find((item) => item.session_id === sessionId)
  const key = `session:${sessionId}`
  agentPanelSections.value = upsertAgentPanelSection(agentPanelSections.value, {
    key,
    type: 'session',
    title: title || session?.title || session?.name || (sessionId === currentChatId.value ? '助理轨迹' : '') || session?.path?.split('/').pop() || '会话轨迹',
    sessionId,
    avatarSeed: sessionId
  })
  agentPanelActiveSectionKey.value = key
  isFilePanelOpen.value = true
  statePanelOpen.value = false
}

const activateAgentPanelSection = (key) => {
  const section = visibleAgentPanelSections.value.find((item) => item.key === key)
  if (!section) return
  agentPanelActiveSectionKey.value = key
  if (section.type === 'file') {
    agentPanelActivePreviewPath.value = section.path
  }
}
const closeAgentPanelSection = (key) => {
  const section = agentPanelSections.value.find((item) => item.key === key)
  if (section?.type === 'file') {
    closePanelPreviewTab(section.path)
    return
  }
  const next = closePanelSectionState(
    agentPanelSections.value,
    agentPanelActiveSectionKey.value,
    key
  )
  agentPanelSections.value = next.sections
  agentPanelActiveSectionKey.value = next.activeKey
  if (!next.sections.length) closeFilePanel()
}

watch(messageDebugEnabled, (enabled) => {
  if (enabled) return
  const hadDebugSection = agentPanelSections.value.some(
    (section) => section.key === MESSAGE_DEBUG_SECTION.key
  )
  if (!hadDebugSection) return
  closeAgentPanelSection(MESSAGE_DEBUG_SECTION.key)
})

const isStateSectionExpanded = (key) => !collapsedStateSections[key]
const toggleStateSection = (key) => {
  collapsedStateSections[key] = !collapsedStateSections[key]
}
const currentStateFiles = computed(() => {
  const files = []
  const seenPaths = new Set()
  const pushFile = (entry, fallbackName = '文件') => {
    const path = String(entry?.path || entry?.file_path || entry?.file_name || entry?.name || '')
    if (!path || seenPaths.has(path)) return
    seenPaths.add(path)
    const name = entry?.file_name || entry?.name || getPanelFileName({ path }) || fallbackName
    files.push({
      key: path,
      path,
      name
    })
  }

  currentThreadAttachments.value.forEach((attachment) => pushFile(attachment, '附件'))

  return files
})
const showStateEntry = computed(() => !effectivePersonalMode.value && !props.embedded && Boolean(currentChatId.value))
const showFileEntry = computed(() => Boolean(currentChatId.value))
const hasVisibleStateSections = computed(
  () =>
    Boolean(currentAgentState.value?.warnings?.length) ||
    Boolean(currentTokenUsage.value) ||
    currentStateFiles.value.length > 0 ||
    currentArtifactFiles.value.length > 0 ||
    cooperationSessions.value.length > 1
)

const { mentionConfig } = useAgentMentionConfig({
  agents: computed(() => effectivePersonalMode.value ? [] : agents.value),
  currentThreadAttachments,
  configurableItems,
  agentConfig
})

const currentThreadMessages = computed(() => threadMessages.value[currentChatId.value] || [])
const currentThreadRuns = computed(() => threadRuns.value[currentChatId.value] || [])
const currentRunById = computed(() => new Map(currentThreadRuns.value.map((run) => [run.id, run])))
const getMessageRun = (message) => currentRunById.value.get(getMessageRunId(message)) || null
const currentThreadHasHistory = computed(() => currentThreadMessages.value.length > 0)
const currentThreadConfigNotice = computed(() => {
  if (!currentChatId.value) return null
  return threadConfigNoticeMap.value[currentChatId.value] || null
})

const visibleApprovalThreadId = computed(() => {
  return currentChatId.value
})
const currentApprovalModalVisible = computed(
  () =>
    approvalState.showModal &&
    Boolean(approvalState.threadId) &&
    approvalState.threadId === visibleApprovalThreadId.value &&
    Boolean(currentThreadState.value?.pendingInterrupt) &&
    approvalState.interruptedRunId === currentThreadState.value.pendingInterrupt.interruptedRunId &&
    approvalState.waitpointId === (currentThreadState.value.pendingInterrupt.waitpointId || null)
)
const currentApprovalQuestions = computed(() =>
  currentApprovalModalVisible.value ? approvalState.questions : []
)
const currentToolApprovalVisible = computed(
  () => currentApprovalModalVisible.value && approvalState.kind === 'tool_approval'
)

const shouldSuppressRefsForApproval = () =>
  currentApprovalModalVisible.value ||
  Boolean(
    approvalState.threadId && currentChatId.value === approvalState.threadId && isProcessing.value
  )

const isRunGroupSettled = (group) =>
  isRunGroupExecutionSettled(runGroups.value, group, isProcessing.value || isReplyLoading.value)

// Run 终态投影落定后才展示 Refs 操作栏与交付物卡片
const shouldShowRefs = computed(() => {
  return (group) => {
    if (!getLastMessage(group) || group.status === 'streaming' || shouldSuppressRefsForApproval()) {
      return false
    }
    return isRunGroupSettled(group)
  }
})

// 当前线程状态的computed属性
const currentThreadState = computed(() => {
  return getThreadState(currentChatId.value)
})

const getThreadOngoingMessages = (threadId) => {
  const threadState = getThreadState(threadId)
  if (!threadState || !threadState.itemState) return []

  const items = Object.values(threadState.itemState.items).filter(
    (item) => item.yuxi?.run_id === threadState.activeRunId
  )
  const visible = itemsToMessages(items).map((item) => ({
    ...item,
    content: threadState.displayText[item.id] ?? item.content
  }))
  const inputIds = new Set(
    visible.filter((item) => item.type === 'human').map((item) => item.input_id)
  )
  const optimistic = Object.values(threadState.optimisticInputs)
    .flat()
    .filter((item) => !inputIds.has(item.extra_metadata?.input_id))
  return [...optimistic, ...visible]
}

const ongoingRunMessages = computed(() => getThreadOngoingMessages(currentChatId.value))

const cooperationToolView = computed(() => {
  const sessions = [
    ...currentThreadMessages.value.flatMap((message) => (message.tool_calls || [])
      .filter((call) => getToolCallId(call) === 'list_sessions')
      .flatMap((call) => parseToolCallResult(call)?.sessions || [])),
    ...props.cooperationMembers,
    ...cooperationSessions.value
  ]
  return {
    sessions,
    treeRootSessionId: sessions.find((member) => member.session_id === currentChatId.value)?.tree_root_session_id || currentChatId.value,
    tasks: collectCooperationTasks(currentThreadMessages.value),
    openSession: navigateToSession,
    openTree: openCooperationTree
  }
})

const runGroups = computed(() => {
  const state = currentThreadState.value
  const messages = currentThreadMessages.value.map((item) => ({
    ...item,
    content: state?.displayText[item.id] ?? item.content
  }))
  const inputIds = new Set(
    messages.filter((item) => item.type === 'human').map((item) => item.input_id)
  )
  const pending = Object.values(state?.optimisticInputs || {}).flat()
  if (effectivePersonalMode.value) {
    for (const input of state?.queuedInputs || []) {
      pending.push(input.message || {
        id: input.input_id, type: 'human', content: input.content || '',
        created_at: input.created_at, delivery_status: input.status,
        extra_metadata: { input_id: input.input_id }
      })
    }
  }
  const optimistic = pending.filter((item) => {
    const inputId = item.extra_metadata?.input_id
    if (inputIds.has(inputId)) return false
    inputIds.add(inputId)
    return true
  })
  const groups = MessageProcessor.convertServerHistoryToMessages(
    effectivePersonalMode.value ? personalMessages([...messages, ...optimistic]) : [...messages, ...optimistic],
    currentThreadRuns.value
  )
  for (const group of groups) {
    if (
      state?.isStreaming &&
      (group.run?.id === state.activeRunId ||
        group.messages.some(
          (item) => getMessageRunId(item) === state.activeRunId || item.id === state.pendingInputId
        ))
    ) {
      group.status = 'streaming'
    }
  }
  return groupRunContinuations(groups)
})

/** 间隔超过一小时时，在新用户消息上方显示发送时间。 */
const getMessageGroupTimeLabel = (group, previousGroup) => {
  const sentAt = group.messages.find((message) => message.type === 'human')?.created_at
  const finishedAt = getMessageRun(
    previousGroup?.messages.findLast((message) => message.type === 'ai')
  )?.timing?.finished_at
  if (!sentAt || !finishedAt) return ''

  // 历史消息的无时区时间来自 PostgreSQL UTC，不能按浏览器本地时间解析。
  const sentTime = dayjs.utc(sentAt)
  const finishedTime = dayjs.utc(finishedAt)
  if (!sentTime.isValid() || !finishedTime.isValid()) return ''
  if (sentTime.valueOf() - finishedTime.valueOf() <= 60 * 60 * 1000) return ''

  const displayTime = parseToShanghai(sentTime.toISOString())
  const today = parseToShanghai(Date.now())
  if (displayTime.isSame(today, 'day')) return displayTime.format('今天 HH:mm')
  if (displayTime.isSame(today.subtract(1, 'day'), 'day')) {
    return displayTime.format('昨天 HH:mm')
  }
  return displayTime.format(displayTime.isSame(today, 'year') ? 'MM-DD HH:mm' : 'YYYY-MM-DD HH:mm')
}

const messageGroupRows = computed(() => {
  const rows = runGroups.value.map((group, index) => ({
    type: 'message-group',
    key:
      group.displayKey ||
      group.run?.id ||
      (group.status === 'streaming' ? 'ongoing-message-group' : `history-${index}`),
    group,
    timeLabel: getMessageGroupTimeLabel(group, runGroups.value[index - 1]),
    displayItems: effectivePersonalMode.value ? getDisplayItems(group).filter((item) => item.type === 'message') : getDisplayItems(group),
    artifacts: MessageProcessor.extractArtifactsFromMessageGroup(group)
  }))

  if (!effectivePersonalMode.value && currentThreadConfigNotice.value) {
    const insertAfterCount = Math.max(
      0,
      Math.min(Number(currentThreadConfigNotice.value.insertAfterGroupCount) || 0, rows.length)
    )
    rows.splice(insertAfterCount, 0, {
      type: 'notice',
      key: currentThreadConfigNotice.value.id,
      notice: currentThreadConfigNotice.value
    })
  }

  return effectivePersonalMode.value
    ? rows.filter((row) => row.displayItems.length || row.group.run?.status === 'failed')
    : rows
})

const isStreaming = computed(() => {
  const threadState = currentThreadState.value
  return threadState ? threadState.isStreaming : false
})
const currentQueuedInputs = computed(() => currentThreadState.value?.queuedInputs || [])
const currentQueueSnapshot = computed(
  () => currentThreadState.value?.queueSnapshot || IDLE_QUEUE_SNAPSHOT
)
// 当前首条输入已有消息气泡，等待消费时只在回复位置提供取消操作。
const primaryQueuedInput = computed(() => {
  const state = currentThreadState.value
  if (currentQueueSnapshot.value.status === 'paused') return null
  return currentQueuedInputs.value.find((input) =>
    input.input_id === state?.pendingInputId &&
    state?.optimisticInputs?.[input.input_id]?.length &&
    input.attachment_status !== 'preparing'
  ) || null
})
const visibleQueuedInputs = computed(() =>
  currentQueuedInputs.value.filter((input) => input !== primaryQueuedInput.value)
)
const queuedInputCount = computed(() => currentQueuedInputs.value.length)
const hasQueuedInputs = computed(() => queuedInputCount.value > 0)
const isWaitingForUserAction = computed(() =>
  isThreadWaitingForUserAction(currentThreadState.value)
)
const queuePausedMessage = '后续队列已暂停。发送新消息将优先处理并继续队列。'
const hasDraftContent = computed(
  () => Boolean(String(userInput.value || '').trim() || agentInputAreaRef.value?.hasImages)
)
const shouldShowStopButton = computed(
  () =>
    !effectivePersonalMode.value && !props.readOnly &&
    (isStreaming.value || currentThreadState.value?.cooperationWaiting) &&
    !hasDraftContent.value
)
const inputSubmissionBlocked = computed(
  () =>
    props.readOnly ||
    !agentExecutionAvailable.value ||
    sendCooldownActive.value ||
    isLoadingMessages.value ||
    isWaitingForUserAction.value ||
    !!pendingSends.value[currentChatId.value] ||
    Boolean(currentThreadState.value?.cancellationPending)
)
const canSubmitSteer = computed(
  () =>
    !inputSubmissionBlocked.value &&
    isStreaming.value &&
    Boolean(currentThreadState.value?.activeRunId) &&
    !currentThreadState.value?.cooperationWaiting &&
    currentThreadState.value?.activeRunSteerable === true &&
    hasDraftContent.value
)
const canCancelQueuedInput = (input) => input?.status !== 'sending'
const canPromoteQueuedInput = (input) =>
  agentExecutionAvailable.value &&
  input?.status === 'pending' &&
  input.kind === 'follow_up' &&
  input.attachment_status !== 'preparing' &&
  !isWaitingForUserAction.value &&
  !currentThreadState.value?.cooperationWaiting &&
  (!currentThreadState.value?.activeRunId || currentThreadState.value?.activeRunSteerable === true)
const shouldRefreshStateWhileStreaming = computed(
  () =>
    workspaceActive.value &&
    Boolean(currentChatId.value) &&
    isStreaming.value &&
    statePanelOpen.value
)
const activeAgentPanelSection = computed(() =>
  agentPanelSections.value.find((section) => section.key === agentPanelActiveSectionKey.value)
)
const activeAgentPanelPreview = computed(() =>
  agentPanelPreviewTabs.value.find((file) => file.path === agentPanelActivePreviewPath.value)
)
const agentPanelFilesystemVisible = computed(
  () =>
    workspaceActive.value &&
    isFilePanelOpen.value &&
    (activeAgentPanelSection.value?.type === 'file-tree' ||
      (activeAgentPanelSection.value?.type === 'file' &&
        activeAgentPanelPreview.value?.workdir === true))
)
const agentPanelFilesystemPollingActive = computed(() =>
  shouldPollAgentPanelFilesystem({
    panelOpen: isFilePanelOpen.value,
    pageVisible: workspaceActive.value && pageVisible.value,
    streaming: isStreaming.value,
    activeSection: activeAgentPanelSection.value,
    activePreview: activeAgentPanelPreview.value
  })
)
const isProcessing = computed(
  () =>
    isStreaming.value ||
    currentThreadState.value?.cooperationWaiting ||
    currentThreadState.value?.cancellationPending ||
    (hasQueuedInputs.value && currentQueueSnapshot.value.status !== 'paused')
)
const isReplyLoading = computed(() => {
  return (
    currentThreadState.value?.cancellationPending ||
    (isProcessing.value && !isWaitingForUserAction.value)
  )
})
const replyLoadingText = computed(() => {
  const threadState = currentThreadState.value
  if (threadState?.cancellationPending) return '正在停止，等待任务清理完成...'
  if (threadState?.cooperationWaiting) return '等待协作任务完成...'
  if (threadState?.contextCompressing) return '正在压缩上下文...'
  if (!isStreaming.value && hasQueuedInputs.value && !primaryQueuedInput.value)
    return `排队中（${queuedInputCount.value} 条）...`
  return '正在生成回复...'
})
// 身份栏只展示当前会话已有的可观察状态。
watch(
  () => {
    if (!currentChatId.value || isSessionLoading.value || sessionAgentStatus.value === 'loading') return '正在加载对话'
    if (sessionAgentStatus.value === 'error' || sessionAgentStatus.value === 'unavailable') return '助理暂不可用'
    if (currentThreadState.value?.cancellationPending) return '正在停止'
    if (isWaitingForUserAction.value) return '等待你的回应'
    if (currentThreadState.value?.cooperationWaiting) return '等待协作结果'
    if (isStreaming.value) return '正在处理'
    return '对话已就绪'
  },
  (status) => emit('status-change', status),
  { immediate: true }
)

const isSendButtonDisabled = computed(
  () =>
    inputSubmissionBlocked.value ||
    (props.sendDisabled && !(effectivePersonalMode.value && canSubmitSteer.value) && !shouldShowStopButton.value) ||
    (!hasDraftContent.value && (effectivePersonalMode.value || !isProcessing.value))
)

const startSendCooldown = () => {
  sendCooldownActive.value = true
  if (sendCooldownTimer) {
    clearTimeout(sendCooldownTimer)
  }
  sendCooldownTimer = setTimeout(() => {
    sendCooldownActive.value = false
    sendCooldownTimer = null
  }, 2000)
}

const CONFIG_CHANGE_NOTICE_MESSAGE =
  '在运行过程中切换或修改配置可能会影响最终效果，建议新建一个对话。'

const withConfigNoticeSync = async (task) => {
  configNoticeSyncDepth.value += 1
  try {
    return await task()
  } finally {
    configNoticeSyncDepth.value = Math.max(0, configNoticeSyncDepth.value - 1)
  }
}

const buildThreadConfigSnapshot = () => {
  return {
    agentId: currentAgentId.value || '',
    configJson: JSON.stringify(agentConfig.value || {})
  }
}

const syncThreadConfigSnapshot = (threadId, options = {}) => {
  if (!threadId) return

  const { overwrite = true } = options
  if (!overwrite && threadConfigSnapshotMap.value[threadId]) return
  if (threadPendingConfigNoticeMap.value[threadId]) return

  // 线程切换时先记录当前 UI 的配置快照，避免同步 thread 绑定配置时误报。
  threadConfigSnapshotMap.value = {
    ...threadConfigSnapshotMap.value,
    [threadId]: buildThreadConfigSnapshot()
  }
}

const upsertThreadConfigNotice = (threadId, insertAfterGroupCount) => {
  if (!threadId) return

  const existingNotice = threadConfigNoticeMap.value[threadId]
  const nextNotice = {
    id: existingNotice?.id || `config-change-notice-${threadId}`,
    message: existingNotice?.message || CONFIG_CHANGE_NOTICE_MESSAGE,
    insertAfterGroupCount
  }
  const shouldScroll =
    !existingNotice || existingNotice.insertAfterGroupCount !== insertAfterGroupCount

  threadConfigNoticeMap.value = {
    ...threadConfigNoticeMap.value,
    [threadId]: nextNotice
  }

  if (threadPendingConfigNoticeMap.value[threadId]) {
    const nextPendingNotices = { ...threadPendingConfigNoticeMap.value }
    delete nextPendingNotices[threadId]
    threadPendingConfigNoticeMap.value = nextPendingNotices
  }

  if (shouldScroll) {
    configNoticeScrollVersion.value += 1
  }
}

const queuePendingThreadConfigNotice = (threadId) => {
  if (!threadId) return
  threadPendingConfigNoticeMap.value = {
    ...threadPendingConfigNoticeMap.value,
    [threadId]: {
      id: `config-change-notice-${threadId}`,
      message: CONFIG_CHANGE_NOTICE_MESSAGE
    }
  }
}

const flushPendingThreadConfigNotice = (threadId) => {
  if (
    !threadId ||
    !currentThreadHasHistory.value ||
    !threadPendingConfigNoticeMap.value[threadId]
  ) {
    return
  }

  upsertThreadConfigNotice(threadId, runGroups.value.length)
}

const maybeInsertThreadConfigNotice = () => {
  const threadId = currentChatId.value
  if (!threadId || configNoticeSyncDepth.value > 0) {
    return
  }

  const previousSnapshot = threadConfigSnapshotMap.value[threadId]
  const currentSnapshot = buildThreadConfigSnapshot()

  if (!previousSnapshot) {
    threadConfigSnapshotMap.value = {
      ...threadConfigSnapshotMap.value,
      [threadId]: currentSnapshot
    }
    return
  }

  if (
    previousSnapshot.agentId === currentSnapshot.agentId &&
    previousSnapshot.configJson === currentSnapshot.configJson
  ) {
    return
  }

  if (currentThreadHasHistory.value) {
    upsertThreadConfigNotice(threadId, runGroups.value.length)
  } else if (isLoadingMessages.value) {
    // 历史线程仍在加载时先挂起提示，避免消息返回后把变更误当成新的基线。
    queuePendingThreadConfigNotice(threadId)
  } else {
    return
  }

  threadConfigSnapshotMap.value = {
    ...threadConfigSnapshotMap.value,
    [threadId]: currentSnapshot
  }
}

// ==================== SCROLL & RESIZE HANDLING ====================
const chatMainRef = ref(null)
const scrollController = new ScrollController(() =>
  workspaceActive.value ? chatMainRef.value : null
)
let retainedScrollTop = 0
const earlierMessagesLoading = ref(false)
const earlierMessagesError = ref(false)
const earlierMessagesLabel = computed(() => {
  if (historyPages.value[currentChatId.value]?.loading || earlierMessagesLoading.value) {
    return '正在加载更早消息…'
  }
  return earlierMessagesError.value ? '加载失败，点击重试' : '加载更早消息'
})

/** 追加历史后按可见消息恢复位置，保留请求期间用户的滚动。 */
const loadEarlierMessages = async () => {
  const threadId = currentChatId.value
  const container = chatMainRef.value
  const paging = historyPages.value[threadId]
  if (
    !container ||
    !workspaceActive.value ||
    !pageVisible.value ||
    isLoadingMessages.value ||
    !paging?.hasMore ||
    paging.loading ||
    earlierMessagesLoading.value
  ) {
    return
  }
  earlierMessagesLoading.value = true
  earlierMessagesError.value = false
  scrollController.disableAutoScroll()
  scrollController.cancelPendingScrolls()
  const anchor = [...container.querySelectorAll('.history-display-item')].find(
    (element) => element.getBoundingClientRect().bottom > container.getBoundingClientRect().top
  )
  const anchorKey = anchor?.dataset.historyKey
  const anchorTop = anchor?.getBoundingClientRect().top
  const previousHeight = container.scrollHeight
  const previousScrollTop = container.scrollTop
  try {
    await fetchThreadMessages({ threadId, more: true })
    await nextTick()
    if (
      currentChatId.value !== threadId ||
      chatMainRef.value !== container ||
      !workspaceActive.value
    ) {
      return
    }
    const currentAnchor = anchor?.isConnected
      ? anchor
      : [...container.querySelectorAll('.history-display-item')].find(
          (element) => element.dataset.historyKey === anchorKey
        )
    const offset = currentAnchor
      ? currentAnchor.getBoundingClientRect().top -
        anchorTop +
        container.scrollTop -
        previousScrollTop
      : container.scrollHeight - previousHeight
    container.scrollTop += offset
    retainedScrollTop = container.scrollTop
  } catch {
    // Runtime 已提供错误提示；停止自动重试，交由用户点击恢复。
    if (currentChatId.value === threadId) earlierMessagesError.value = true
  } finally {
    earlierMessagesLoading.value = false
  }
}

const handleWorkspaceScroll = () => {
  if (!workspaceActive.value) return
  if (scrollController.hasLayoutChanged()) return
  const scrollTop = chatMainRef.value?.scrollTop || 0
  const scrollingUp = scrollTop < retainedScrollTop
  retainedScrollTop = scrollTop
  scrollController.handleScroll()
  if (scrollingUp && scrollTop <= 600 && !earlierMessagesError.value) {
    void loadEarlierMessages()
  }
}
const messageInputDockRef = ref(null)
let chatMainResizeObserver = null
// 初始化延迟标志，避免首次挂载时 ResizeObserver 立即触发导致侧边栏意外关闭
let isResizeObserverReady = false
let resizeObserverReadyTimer = null

const armResizeObserver = (onReady) => {
  if (resizeObserverReadyTimer) {
    clearTimeout(resizeObserverReadyTimer)
  }

  isResizeObserverReady = false
  // keep-alive 切页回来时等布局稳定后再恢复宽度判断，避免隐藏态宽度污染侧边栏状态。
  resizeObserverReadyTimer = setTimeout(() => {
    isResizeObserverReady = true
    onReady?.()
  }, 50)
}

const stopChatMainResizeObserver = () => {
  if (resizeObserverReadyTimer) {
    clearTimeout(resizeObserverReadyTimer)
    resizeObserverReadyTimer = null
  }

  isResizeObserverReady = false

  if (chatMainResizeObserver) {
    chatMainResizeObserver.disconnect()
    chatMainResizeObserver = null
  }
}

const stopStreamingStateRefresh = () => {
  if (streamingStateRefreshTimer) {
    clearInterval(streamingStateRefreshTimer)
    streamingStateRefreshTimer = null
  }
}

const startStreamingStateRefresh = () => {
  stopStreamingStateRefresh()
  streamingStateRefreshTimer = setInterval(() => {
    if (!shouldRefreshStateWhileStreaming.value) return
    void handleAgentStateRefresh()
  }, 5000)
}

const startChatMainResizeObserver = () => {
  if (!chatMainRef.value || chatMainResizeObserver) {
    return
  }

  const syncLayoutMetrics = () => {
    localUIState.chatMainWidth = chatMainRef.value?.clientWidth || window.innerWidth
    localUIState.chatContentWidth =
      chatContentContainerRef.value?.clientWidth || localUIState.chatMainWidth

    const main = chatMainRef.value?.getBoundingClientRect()
    const panel = isFilePanelOpen.value ? panelWrapperRef.value?.getBoundingClientRect() : null
    // 窄屏的主区有最小宽度，实际可见区域还须扣除覆盖它的轨迹面板。
    localUIState.chatMainVisibleWidth = main
      ? Math.max(0, Math.min(main.right, panel?.left ?? main.right) - main.left)
      : 0

    updateStatePanelPlacement()
  }

  syncLayoutMetrics()
  if (!window.ResizeObserver) return

  chatMainResizeObserver = new ResizeObserver((entries) => {
    // 初始化期间跳过检查，等待 layout 稳定
    if (!isResizeObserverReady) return

    if (!entries.length) return
    if (!earlierMessagesLoading.value) {
      scrollController.handleResize()
      retainedScrollTop = chatMainRef.value?.scrollTop || 0
    }
    syncLayoutMetrics()
  })
  if (panelWrapperRef.value) chatMainResizeObserver.observe(panelWrapperRef.value)
  chatMainResizeObserver.observe(chatMainRef.value)
  const chatBox = chatMainRef.value.querySelector('.chat-box')
  if (chatBox) chatMainResizeObserver.observe(chatBox)
  if (chatContentContainerRef.value) {
    chatMainResizeObserver.observe(chatContentContainerRef.value)
  }
  if (messageInputDockRef.value) {
    chatMainResizeObserver.observe(messageInputDockRef.value)
  }
  armResizeObserver(() => {
    scrollController.rememberReadingPosition()
    syncLayoutMetrics()
  })
}

onMounted(() => {
  if (typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', handlePageVisibilityChange)
  }

  nextTick(() => {
    const chatMainContainer = chatMainRef.value
    if (chatMainContainer) {
      chatMainContainer.addEventListener('scroll', handleWorkspaceScroll, { passive: true })
    }

    startChatMainResizeObserver()
  })
})

onActivated(() => {
  workspaceActivated.value = true
  nextTick(() => {
    startChatMainResizeObserver()
    if (chatMainRef.value) chatMainRef.value.scrollTop = retainedScrollTop
  })
})

onDeactivated(() => {
  workspaceActivated.value = false
  stopChatMainResizeObserver()
  stopStreamingStateRefresh()
  scrollController.cleanup()
  if (props.isNewSession && currentChatId.value) {
    // 正式路由接管线程草稿；缓存的新建页不能在再次打开时覆写它。
    userInput.value = threadDraftSession.switchThread('', userInput.value)
  }
})

onUnmounted(() => {
  if (typeof document !== 'undefined') {
    document.removeEventListener('visibilitychange', handlePageVisibilityChange)
  }
  scrollController.cleanup()
  stopChatMainResizeObserver()
  stopStreamingStateRefresh()
  if (sendCooldownTimer) {
    clearTimeout(sendCooldownTimer)
    sendCooldownTimer = null
  }
  for (const entry of agentPanelPreviewCache.values()) {
    if (entry.file?.previewUrl) window.URL.revokeObjectURL(entry.file.previewUrl)
  }
  agentPanelPreviewCache.clear()
})

// ==================== 线程管理方法 ====================
// 获取当前智能体的线程列表
const fetchThreads = async (agentId = null) => {
  await chatThreadsStore.loadThreads(agentId)
}

// 把草稿线程的选择迁移到真实线程：真实线程未设值时才覆盖，迁移后删除草稿。
const promoteDraftSelection = (selectionByThread, threadId) => {
  const draft = selectionByThread[DRAFT_MODEL_KEY]
  if (!draft) return
  if (!selectionByThread[threadId]) selectionByThread[threadId] = draft
  delete selectionByThread[DRAFT_MODEL_KEY]
}

const fetchThreadAttachments = async (threadId) => {
  if (!threadId) return
  try {
    const response = await threadApi.getThreadAttachments(threadId)
    threadAttachmentsMap.value[threadId] = Array.isArray(response?.attachments)
      ? response.attachments
      : []
  } catch (error) {
    console.warn('Failed to fetch thread attachments:', error)
    threadAttachmentsMap.value[threadId] = []
  }
}

const handleArtifactSaved = async () => {
  if (!currentChatId.value) return
  await fetchThreadAttachments(currentChatId.value)
  agentPanelFilesystemRefreshVersion.value += 1
  showFileTreePanel()
}

const invalidateAgentStateRequest = (threadId) => {
  const threadState = getThreadState(threadId)
  if (!threadState) return
  threadState.agentStateRequestVersion = (threadState.agentStateRequestVersion || 0) + 1
}

const createActiveThread = async () => {
  if (currentChatId.value) return currentChatId.value
  const selectedAgent = currentAgentId.value
  const selectedProject = selectedProjectId.value
  const startingThreadId = currentChatId.value
  const creationContext = {
    agentId: selectedAgent,
    projectId: selectedProject,
    threadId: startingThreadId
  }
  let request = threadCreationRequest.value
  if (!request || request.agentId !== selectedAgent || request.projectId !== selectedProject) {
    request = {
      ...creationContext,
      id: createInputRequestId(),
      metadata: { tool_approval_mode: currentToolApprovalMode.value }
    }
    threadCreationRequest.value = request
  }
  chatThreadsStore.setThreadCreationInFlight(true)
  try {
    const projectId = selectedProject === AUTO_PROJECT_ID ? '' : selectedProject
    const { thread, accepted } = await createThreadForContext({
      context: creationContext,
      getCurrentContext: () => ({
        agentId: currentAgentId.value,
        projectId: selectedProjectId.value,
        threadId: currentChatId.value
      }),
      requestId: request.id,
      create: (stableRequestId) =>
        chatThreadsStore.createThread(selectedAgent, null, request.metadata, {
          requestId: stableRequestId,
          projectId: projectId || undefined
        })
    })
    if (!thread) throw new Error('创建对话失败')
    if (!accepted) throw new Error('新对话上下文已变化，请重新发送或添加附件')

    getThreadState(thread.id).historyLoaded = true
    threadAttachmentsMap.value[thread.id] = []
    threadCreationRequest.value = null
    promoteDraftSelection(draftFilesByThread.value, thread.id)
    if (attachmentUploadThreadKey.value === DRAFT_MODEL_KEY) {
      attachmentUploadThreadKey.value = thread.id
    }
    setCurrentThreadId(thread.id, { force: true })
    await refreshSessionAgent(thread.id, selectedAgent)
    return thread.id
  } catch (error) {
    if (error.status >= 400 && error.status < 500 && error.status !== 429)
      threadCreationRequest.value = null
    throw error
  } finally {
    chatThreadsStore.setThreadCreationInFlight(false)
  }
}

const ensureActiveThread = createSingleFlight(createActiveThread)

const handleAttachmentUpload = async (files = []) => {
  if (
    !AgentValidator.validateAgentIdWithError(
      currentAgentId.value,
      '上传附件',
      handleValidationError
    )
  )
    return

  if (!attachmentUploadModalOpen.value) {
    attachmentUploadThreadKey.value = currentChatId.value || DRAFT_MODEL_KEY
  }
  const droppedFiles = Array.from(files || []).filter((file) => file instanceof File)
  if (droppedFiles.length) {
    attachmentInitialFiles.value = droppedFiles
    attachmentInitialFilesKey.value += 1
  }

  attachmentUploadModalOpen.value = true
}

const handleTmpAttachmentsAdded = (files) => {
  const key = attachmentUploadThreadKey.value
  draftFilesByThread.value[key] = [...(draftFilesByThread.value[key] || []), ...files]
}

const handleAttachmentRemove = async (file) => {
  const key = currentChatId.value || DRAFT_MODEL_KEY
  try {
    await threadApi.deleteDraftFile(file.id)
    draftFilesByThread.value[key] = (draftFilesByThread.value[key] || []).filter(
      (item) => item.id !== file.id
    )
  } catch (error) {
    handleChatError(error, 'delete')
  }
}

// ==================== 审批功能管理 ====================
const { approvalState, restoreInterruptFromThreadState, hideApprovalState } = useApproval({
  getThreadState,
  fetchThreadMessages,
  getVisibleThread: () => visibleApprovalThreadId.value
})

const restorePendingInterruptForThread = (threadId) => {
  if (!threadId) return false
  return restoreInterruptFromThreadState(threadId)
}
watch(visibleApprovalThreadId, (threadId) => {
  if (!restorePendingInterruptForThread(threadId)) hideApprovalState()
})

provide('getAgentThreadState', getThreadState)
const runtimeView = {
  onScrollToBottom: () => {
    if (workspaceActive.value && pageVisible.value) scrollController.scrollToBottom()
  },
  onInterruptDetected: ({ threadId }) => {
    if (workspaceActive.value) restorePendingInterruptForThread(threadId)
  },
  onTerminalDetected: ({ threadId, runId }) => {
    if (approvalState.threadId === threadId) hideApprovalState()
    if (runId && threadId === currentThreadId.value) {
      agentPanelFilesystemRefreshVersion.value += 1
    }
    if (workspaceActive.value && pageVisible.value && runId && threadId === currentThreadId.value) {
      void chatThreadsStore.markThreadViewed(threadId)
    }
  }
}
let releaseRuntimeView = () => {}
watch(
  () =>
    userStore.isLoggedIn && !(props.isNewSession && !workspaceActive.value)
      ? currentThreadId.value
      : null,
  (threadId) => {
    releaseRuntimeView()
    releaseRuntimeView = sessionRuntime.observeThread(threadId, runtimeView)
  },
  { immediate: true, flush: 'sync' }
)
onUnmounted(() => releaseRuntimeView())

const handlePromoteQueuedInput = async (inputId) => {
  const threadId = currentChatId.value
  if (!threadId || promotingInputIds.has(inputId)) return
  promotingInputIds.add(inputId)
  try {
    if (await promoteInput(threadId, inputId)) message.success('已转为引导')
  } finally {
    promotingInputIds.delete(inputId)
  }
}

const handleCancelQueuedInput = async (inputId) => {
  const threadId = currentChatId.value
  if (!threadId || !inputId || cancellingInputIds.has(inputId)) return

  cancellingInputIds.add(inputId)
  const cancelled = await cancelInput(threadId, inputId)
  cancellingInputIds.delete(inputId)
  if (cancelled) {
    await resumeQueuedInputs(threadId)
    message.success('已取消排队输入')
  }
}

const handleContinueQueue = async () => {
  if (!agentExecutionAvailable.value) return
  const threadId = currentChatId.value
  if (!threadId || currentThreadState.value?.continueQueueInFlight) return

  if (await continueQueue(threadId)) {
    message.success('队列已继续')
  }
}

const resumeCurrentRunForVisiblePage = async () => {
  if (!workspaceActive.value) return
  if (typeof document !== 'undefined' && document.visibilityState !== 'visible') return
  const threadId = currentChatId.value
  if (!threadId) return

  await refreshSessionAgent(threadId, currentAgentId.value)
  if (!agentExecutionAvailable.value) return
  try {
    await resumeActiveRunForThread(threadId)
    await resumeQueuedInputs(threadId)
    restorePendingInterruptForThread(threadId)
  } catch (error) {
    console.warn('Failed to resume current run after page became visible:', error)
  }
}

watch(workspaceActive, (active) => {
  if (!active) return
  void resumeCurrentRunForVisiblePage()
  if (pageVisible.value && currentChatId.value) {
    void chatThreadsStore.markThreadViewed(currentChatId.value)
  }
})

const handlePageVisibilityChange = () => {
  pageVisible.value = typeof document === 'undefined' || document.visibilityState === 'visible'
  if (!pageVisible.value) return
  void resumeCurrentRunForVisiblePage()
}

// ==================== CHAT ACTIONS ====================
const selectChat = async (chatId) => {
  if (threadCreationInFlight.value) {
    message.info('正在创建新对话，请稍候')
    return false
  }
  const targetChat = threads.value.find((chat) => chat.id === chatId) || null
  const targetAgentId = targetChat?.agent.id || currentAgentId.value
  const previousThreadId = currentThreadId.value

  if (!targetAgentId) {
    handleValidationError('选择对话失败：缺少智能体信息')
    return
  }

  if (!AgentValidator.validateAgentIdWithError(targetAgentId, '选择对话', handleValidationError))
    return

  if (previousThreadId !== chatId) {
    resetAgentPanelState()
  }

  setCurrentThreadId(chatId)
  const availability = refreshSessionAgent(chatId, targetAgentId)

  isLoadingMessages.value = true
  try {
    await fetchThreadMessages({ threadId: chatId })
    if (workspaceActive.value && pageVisible.value && currentThreadId.value === chatId) {
      void chatThreadsStore.markThreadViewed(chatId)
    }
  } catch (error) {
    handleChatError(error, 'load')
    return [403, 404, 410].includes(error?.status) ? false : null
  } finally {
    isLoadingMessages.value = false
  }

  await availability
  if (currentChatId.value !== chatId) return false
  await nextTick()
  await scrollController.scrollToBottomStaticForce()
  if (!agentExecutionAvailable.value) return true
  await withConfigNoticeSync(async () => syncThreadConfigSnapshot(chatId))
  await handleAgentStateRefresh(chatId)
  syncThreadConfigSnapshot(chatId, { overwrite: false })
  await resumeActiveRunForThread(chatId)
  await resumeQueuedInputs(chatId)
  restorePendingInterruptForThread(chatId)
  await scrollController.scrollToBottomStaticForce()
  return true
}

const selectThreadFromRoute = async (threadId) => {
  if (threadCreationInFlight.value) return null
  if (!agentStore.isInitialized) {
    await initAll()
  }

  if (!threadId) {
    const previousThreadId = currentThreadId.value
    resetAgentPanelState()
    setCurrentThreadId(null)
    if (previousThreadId) emit('thread-change', '')
    return true
  }

  const loadedCurrentThread = currentThreadId.value === threadId && getThreadState(threadId).historyLoaded
  if (loadedCurrentThread || !threads.value.some((thread) => thread.id === threadId)) {
    try {
      chatThreadsStore.upsertThread(await agentApi.getPublicThread(threadId))
    } catch (error) {
      handleChatError(error, 'load')
      return [403, 404, 410].includes(error?.status) ? false : null
    }
  }

  const targetThread = threads.value.find((thread) => thread.id === threadId)
  if (!targetThread) {
    return false
  }

  if (loadedCurrentThread) {
    if (!props.embedded && !effectivePersonalMode.value && workspaceActive.value) chatThreadsStore.setCurrentThreadId(threadId)
    await resumeCurrentRunForVisiblePage()
    return true
  }

  return selectChat(threadId)
}

const sessionInput = createSessionInput({
  runtime: sessionRuntime,
  onPrepared: (_threadId, draft) => {
    if (!draft.fromWidget && userInput.value.trim() === draft.text) userInput.value = ''
    if (approvalState.threadId === _threadId) hideApprovalState()
    void nextTick(() => scrollController.scrollToBottom(true))
  },
  onAccepted: async (threadId, draft) => {
    draft.onWidgetAccepted?.()
    draftFilesByThread.value[threadId] = (draftFilesByThread.value[threadId] || []).filter(
      (item) => !draft.data.attachment_file_ids.includes(item.id)
    )
    const model = draft.data.model_spec
    const thread = threads.value.find((item) => item.id === threadId)
    if (model && thread) {
      thread.agent.model = model
      delete selectedModelByThread[threadId]
      void chatThreadsStore
        .updateThread(threadId, null, undefined, undefined, model)
        .catch(() => {})
    }
    if (draft.createdFromDraft && currentChatId.value === threadId)
      threadDraftSession.clearDraftThread()
    if (props.isNewSession && currentChatId.value === threadId) emit('thread-change', threadId)
    await fetchThreadAttachments(threadId)
  },
  onRejected: async (threadId, draft, error, { accepted, restore }) => {
    if (restore && !draft.fromWidget && currentChatId.value === threadId) {
      userInput.value = [draft.text, userInput.value].filter(Boolean).join('\n')
      agentInputAreaRef.value?.restoreImages?.(draft.images)
    }
    if (isRunInterruptedConflict(error)) {
      try {
        await fetchAgentState(threadId, { required: true })
      } catch {
        message.error('审批状态恢复失败，请刷新页面后重试')
      }
    }
    if (accepted && props.isNewSession) emit('thread-change', threadId)
    handleChatError(error, 'send')
  }
})

const widgetActionsDisabled = computed(
  () =>
    !agentExecutionAvailable.value ||
    props.sendDisabled ||
    isLoadingMessages.value ||
    isProcessing.value ||
    isWaitingForUserAction.value ||
    sendCooldownActive.value ||
    Boolean(pendingSends.value[currentChatId.value])
)

const handleSendMessage = async ({
  images = [],
  mode = 'follow_up',
  retry = false,
  text: submittedText,
  fromWidget = false,
  onWidgetAccepted
} = {}) => {
  if (props.readOnly) return false
  if (effectivePersonalMode.value) mode = 'steer'
  const original = pendingSends.value[currentChatId.value]
  if ((original && !retry) || (retry && !original)) return false
  const text = original?.text ?? submittedText ?? userInput.value.trim()
  if (
    (!text && !images.length && !original?.images.length) ||
    !agentExecutionAvailable.value ||
    sendCooldownActive.value ||
    (props.sendDisabled && !retry && !(mode === 'steer' && canSubmitSteer.value)) ||
    (effectivePersonalMode.value && isProcessing.value && !canSubmitSteer.value) ||
    isLoadingMessages.value ||
    isWaitingForUserAction.value
  )
    return false
  startSendCooldown()
  let threadId = currentChatId.value
  const createdFromDraft = !threadId
  const draft = {
    text,
    images: images.map((image) => ({ ...image })),
    createdFromDraft,
    fromWidget,
    onWidgetAccepted,
    attachments: fromWidget
      ? []
      : currentPendingThreadAttachments.value.map((file) => ({ ...file })),
    modelSpec: effectivePersonalMode.value ? null : currentModelSpec.value || null,
    toolApprovalMode: effectivePersonalMode.value ? null : currentToolApprovalMode.value,
    mode
  }
  if (!threadId) {
    const consumeDraft = !fromWidget && userInput.value.trim() === text
    if (consumeDraft) userInput.value = ''
    try {
      threadId = await ensureActiveThread()
      if (!threadId) throw new Error('创建对话未返回线程')
    } catch {
      if (consumeDraft) {
        userInput.value = [draft.text, userInput.value].filter(Boolean).join('\n')
        agentInputAreaRef.value?.restoreImages?.(draft.images)
      }
      message.error('创建对话失败，请重试')
      return
    }
    promoteDraftSelection(selectedModelByThread, threadId)
  }
  return sessionInput.send(threadId, draft, { retry })
}

const handleDirectSteer = async () => {
  if (!canSubmitSteer.value) return
  agentInputAreaRef.value?.submit('steer')
}

const handleContextCompression = async () => {
  if (!agentExecutionAvailable.value) return
  const threadId = currentChatId.value
  const threadState = getThreadState(threadId)
  if (
    !threadId ||
    !threadState ||
    isContextCompressionPending.value ||
    isProcessing.value ||
    hasQueuedInputs.value ||
    isWaitingForUserAction.value
  )
    return

  threadState.contextCompressing = true
  try {
    const response = await agentApi.compressThreadContext(threadId)
    await fetchAgentState(threadId)
    if (response?.status === 'completed') message.success('上下文压缩完成')
    else message.info('当前没有足够的历史消息可压缩')
  } catch (error) {
    handleChatError(error, 'compress_context')
  } finally {
    const latestState = getThreadState(threadId)
    if (latestState) latestState.contextCompressing = false
  }
}

// 发送或中断
const handleSendOrStop = async (payload) => {
  if (sendCooldownActive.value) {
    return
  }

  const threadId = currentChatId.value
  const threadState = getThreadState(threadId)
  const hasNewInput = Boolean(String(userInput.value || '').trim() || payload?.images?.length)
  if (
    !effectivePersonalMode.value && !props.readOnly && threadState?.activeRunId &&
    (threadState.isStreaming || threadState.cooperationWaiting) &&
    !hasNewInput
  ) {
    try {
      if (!(await requestTurnCancellation(threadId))) return
      threadState.pendingInterrupt = null
      if (approvalState.threadId === threadId) {
        hideApprovalState()
      }
      message.info('已发送取消请求')
    } catch (error) {
      handleChatError(error, 'stop')
    }
    return
  }
  await handleSendMessage(payload)
}

// ==================== 人工审批处理 ====================
const handleApprovalWithStream = async (answer) => {
  if (approvalSubmitting.value) return
  const threadId = approvalState.threadId
  const interruptedRunId = approvalState.interruptedRunId
  if (!threadId) {
    message.error('无效的提问请求')
    approvalState.showModal = false
    return
  }

  const threadState = getThreadState(threadId)
  if (!threadState) {
    message.error('无法找到对应的对话线程')
    approvalState.showModal = false
    return
  }

  if (!interruptedRunId) {
    message.error('无法找到需要恢复的运行任务')
    approvalState.showModal = false
    return
  }

  threadState.approvalSubmitting = true

  try {
    const turnId = threadState.currentTurnId
    const accepted = await resumeSessionWaitpoint({
      threadId,
      turnId,
      runId: interruptedRunId,
      kind: approvalState.kind,
      answer
    })
    const runId = accepted.run_id
    invalidateAgentStateRequest(threadId)
    hideApprovalState()
    threadState.pendingInterrupt = null
    threadState.isStreaming = true
    resetThreadExecution(threadId, { preserveInputMonitors: true })
    // 首个流事件前读取已持久化的续跑关系；读取失败不能把已创建的 Run 当成创建失败。
    try {
      await fetchThreadMessages({ threadId })
    } catch (error) {
      console.warn('Failed to refresh history before resume stream:', error)
    }
    await startRunStream(threadId, runId, null, { turnId })
  } catch (error) {
    // 接收前保留原审批供重试；失败不能回滚期间推进的 Run 或等待点。
    handleChatError(error, 'resume')
  } finally {
    threadState.approvalSubmitting = false
  }
}

const handleQuestionSubmit = (answer) => {
  handleApprovalWithStream(answer)
}

const handleQuestionCancel = async () => {
  if (approvalSubmitting.value) return
  const threadId = approvalState.threadId
  if (!threadId) return
  const threadState = getThreadState(threadId)
  threadState.approvalSubmitting = true
  try {
    if (!threadState.currentTurnId || !threadState.activeRunId)
      throw new Error('当前线程没有等待中的 Turn')
    if (!(await requestTurnCancellation(threadId))) return
    hideApprovalState()
    getThreadState(threadId).pendingInterrupt = null
    await resumeQueuedInputs(threadId)
    message.info('已请求停止当前任务')
  } catch (error) {
    handleChatError(error, 'stop')
  } finally {
    threadState.approvalSubmitting = false
  }
}

const buildExportPayload = () => {
  const agentId = currentAgentId.value
  let agentDescription = ''
  if (agentId && agents.value && agents.value.length > 0) {
    const agent = agents.value.find((a) => a.agent_id === agentId)
    agentDescription = agent ? agent.description || '' : ''
  }

  const payload = {
    chatTitle: currentThread.value?.yuxi.title || '新对话',
    agentName: currentAgentName.value || currentAgent.value?.name || '智能助手',
    agentDescription: agentDescription || currentAgent.value?.description || '',
    messages: runGroups.value ? JSON.parse(JSON.stringify(runGroups.value)) : [],
    onGoingMessages: ongoingRunMessages.value
      ? JSON.parse(JSON.stringify(ongoingRunMessages.value))
      : []
  }

  return payload
}

defineExpose({
  getExportPayload: buildExportPayload,
  selectThreadFromRoute,
  openSessionFromRoute,
  navigateToSession,
  focusPendingAction
})

/** 重新读取目标会话，为当前人工等待登记一次聚焦意图。 */
async function focusPendingAction() {
  await workspaceReady
  await resumeCurrentRunForVisiblePage()
  if (workspaceActive.value && currentThreadState.value?.turnStatus === 'requires_action') {
    pendingActionFocusTurnId.value = currentThreadState.value.currentTurnId
  }
}

// 等待点在历史恢复后异步到达；离开视图或轮次变化时撤销导航聚焦。
watch(
  [
    currentApprovalModalVisible,
    workspaceActive,
    pendingActionFocusTurnId,
    () => currentThreadState.value?.currentTurnId,
    () => currentThreadState.value?.turnStatus
  ],
  async ([visible, active, requestedTurnId, turnId, turnStatus]) => {
    if (!requestedTurnId) return
    if (!active || requestedTurnId !== turnId || turnStatus !== 'requires_action') {
      pendingActionFocusTurnId.value = null
      return
    }
    if (!visible) return
    await nextTick()
    if (
      workspaceActive.value &&
      currentApprovalModalVisible.value &&
      pendingActionFocusTurnId.value === currentThreadState.value?.currentTurnId
    ) {
      messageInputStageRef.value?.querySelector('[role="dialog"]')?.focus()
      pendingActionFocusTurnId.value = null
    }
  }
)

const handleAgentStateRefresh = async (threadId = null) => {
  if (!currentAgentId.value) return
  const chatId = threadId || currentChatId.value
  if (!chatId) return
  isRefreshingState.value = true
  try {
    await Promise.all([fetchAgentState(chatId), fetchThreadAttachments(chatId)])
  } finally {
    isRefreshingState.value = false
  }
}

const toggleStatePanel = async () => {
  const nextOpen = !statePanelOpen.value
  statePanelOpen.value = nextOpen
  if (nextOpen && currentChatId.value && !currentAgentState.value) {
    await handleAgentStateRefresh()
  }
}

const closeFilePanel = () => {
  isFilePanelOpen.value = false
  isAgentPanelMaximized.value = false
  filePanelDragWidth.value = null
  if (effectivePersonalMode.value && router.currentRoute.value.query.session_id) {
    const query = { ...router.currentRoute.value.query }
    delete query.session_id
    void router.replace({ query })
  }
}

const toggleAgentPanelMaximized = () => {
  if (isResizing.value) return
  isAgentPanelMaximized.value = !isAgentPanelMaximized.value
  if (!isAgentPanelMaximized.value && agentPanelActiveSectionKey.value === 'main-session') {
    agentPanelActiveSectionKey.value = agentPanelSections.value[0]?.key || 'file-tree'
  }
  filePanelDragWidth.value = null
}

const toggleAgentPanel = async () => {
  const nextOpen = !isFilePanelOpen.value

  if (!nextOpen) {
    closeFilePanel()
    return
  }

  showFilePanel(agentPanelActivePreviewPath.value ? 'preview' : 'tree')
  await handleAgentStateRefresh()
}

// 处理面板宽度调整（使用比例）
// 向右拖动(deltaX > 0)让面板变窄，向左拖动(deltaX < 0)让面板变宽
const handlePanelResize = (clientX) => {
  if (!panelWrapperRef.value || isAgentPanelMaximized.value) return

  if (!panelContainerWidth) {
    panelContainerWidth = getPanelContainerWidth()
  }

  const deltaX = clientX - resizeStartX
  const rawWidth = resizeStartWidth - deltaX
  const maxWidth = getFilePanelMaxWidth(panelContainerWidth)
  const minWidth = getFilePanelMinWidth(panelContainerWidth, maxWidth)
  const nextWidth = Math.max(minWidth, Math.min(rawWidth, maxWidth))

  filePanelDragWidth.value = nextWidth

  if (nextWidth !== rawWidth) {
    resizeStartX = clientX
    resizeStartWidth = nextWidth
  }
}

// 拖拽状态变化时，同步最终状态到 Vue 响应式数据
const handleResizingChange = (isResizingState, clientX = 0) => {
  if (isAgentPanelMaximized.value) return
  isResizing.value = isResizingState

  if (isResizingState && panelWrapperRef.value) {
    resizeStartX = clientX
    resizeStartWidth = panelWrapperRef.value.offsetWidth
    filePanelDragWidth.value = resizeStartWidth
    if (!panelContainerWidth) {
      panelContainerWidth = getPanelContainerWidth()
    }
    return
  }

  if (!isResizingState && panelWrapperRef.value && panelContainerWidth) {
    const finalWidth = filePanelDragWidth.value ?? panelWrapperRef.value.offsetWidth
    panelRatio.value = clampPanelRatio(finalWidth / panelContainerWidth, panelContainerWidth)
  }

  if (!isResizingState) {
    filePanelDragWidth.value = null
    resizeStartX = 0
    resizeStartWidth = 0
    panelContainerWidth = 0 // 重置，供下次使用
  }
}

// ==================== HELPER FUNCTIONS ====================
const getMessageToolCalls = (message) => normalizeToolCalls(message?.tool_calls)

const getDisplayItems = (group) =>
  getMessageGroupDisplayItems(group, {
    enrichToolCalls: getMessageToolCalls,
    runTiming: group.processTiming || getMessageRun(getLastMessage(group))?.timing,
    collapseIntermediate: !effectivePersonalMode.value && group?.status !== 'streaming' && isRunGroupSettled(group)
  })

const isDisplayMessageProcessing = (group, displayItem) => {
  return (
    displayItem?.type === 'message' &&
    isReplyLoading.value &&
    group?.status === 'streaming' &&
    displayItem.sourceIndex === group.messages.length - 1
  )
}

const isToolGroupActive = (group, itemIndex, displayItems) => {
  return (
    isReplyLoading.value && group?.status === 'streaming' && itemIndex === displayItems.length - 1
  )
}

const getLastMessage = (group) => {
  if (!group?.messages?.length) return null
  for (let i = group.messages.length - 1; i >= 0; i--) {
    if (group.messages[i].type === 'ai') return group.messages[i]
  }
  return null
}

const showMsgRefs = (msg, group) => {
  if (shouldSuppressRefsForApproval()) {
    return false
  }

  // 该消息所在对话未收尾（后面跟的是没有 human message 的 AI 续写，或仍在生成）时不展示
  if (!isRunGroupSettled(group)) {
    return false
  }

  // 只有真正完成的消息才显示 refs
  if (msg.isLast && msg.status === 'finished') {
    return ['copy', 'sources']
  }
  return false
}

const getMessageGroupSources = (group) => {
  return MessageProcessor.extractSourcesFromMessageGroup(group, availableKnowledgeBases.value)
}

// ==================== LIFECYCLE & WATCHERS ====================
const loadChatsList = async () => {
  try {
    await fetchThreads()

    // 首页缺席不代表删除；当前会话可能位于后续分页。
    if (currentThreadId.value && !threads.value.find((t) => t.id === currentThreadId.value)) {
      chatThreadsStore.upsertThread(await agentApi.getPublicThread(currentThreadId.value))
    }
  } catch (error) {
    handleChatError(error, 'load')
  }
}

const initAll = async () => {
  try {
    if (!agentStore.isInitialized) {
      await agentStore.initialize()
    }
  } catch (error) {
    handleChatError(error, 'load')
  }
}

/** 初始化视图，待办导航等待成员首次装载完成后再聚焦。 */
async function initializeWorkspace() {
  await initAll()
  if (props.embeddedThreadId) {
    await selectThreadFromRoute(props.embeddedThreadId)
    closeFilePanel()
  }
  scrollController.enableAutoScroll()
}

onMounted(() => {
  workspaceReady = initializeWorkspace()
})

watch(showStateEntry, (visible) => {
  if (!visible && statePanelOpen.value) {
    statePanelOpen.value = false
  }
})

watch(showFileEntry, (visible) => {
  if (!visible && isFilePanelOpen.value) {
    closeFilePanel()
  }
})

watch(
  shouldRefreshStateWhileStreaming,
  (shouldRefresh) => {
    if (shouldRefresh) {
      void handleAgentStateRefresh()
      startStreamingStateRefresh()
    } else {
      stopStreamingStateRefresh()
    }
  },
  { immediate: true }
)

watch(
  currentAgentId,
  async (_newAgentId, oldAgentId) => {
    if (!props.embedded && oldAgentId === undefined) {
      await loadChatsList()
    }
  },
  { immediate: true }
)

watch(
  currentThreadMessages,
  () => {
    if (currentThreadHasHistory.value) {
      flushPendingThreadConfigNotice(currentChatId.value)
      syncThreadConfigSnapshot(currentChatId.value, { overwrite: false })
    }
  },
  { deep: false }
)

watch(currentAgentId, (newAgentId, oldAgentId) => {
  if (oldAgentId === undefined || newAgentId === oldAgentId) return
  maybeInsertThreadConfigNotice()
})

watch(
  () => JSON.stringify(agentConfig.value || {}),
  (newConfigJson, oldConfigJson) => {
    if (oldConfigJson === undefined || newConfigJson === oldConfigJson) return
    maybeInsertThreadConfigNotice()
  }
)

watch(
  runGroups,
  () => {
    if (workspaceActive.value && isProcessing.value) {
      scrollController.scrollToBottom()
    }
  },
  { flush: 'post' }
)

watch(
  configNoticeScrollVersion,
  () => {
    if (!workspaceActive.value || !currentChatId.value) return
    scrollController.scrollToBottom(true)
  },
  { flush: 'post' }
)

watch(currentChatId, (threadId, oldThreadId) => {
  if (threadId === oldThreadId) return
  earlierMessagesError.value = false
  // 旧线程已被删除时丢弃输入草稿，避免写入无法再次访问的孤儿缓存
  const keepInput = !oldThreadId || threads.value.some((thread) => thread.id === oldThreadId)
  // 切换线程：保存旧线程的输入草稿，并还原新线程（或新建对话）的草稿
  userInput.value = threadDraftSession.switchThread(threadId, keepInput ? userInput.value : '')
  if (!threadId || approvalState.threadId !== threadId) {
    hideApprovalState()
  }
  if (!threadId) {
    ensureActiveThread.reset()
    selectedProjectId.value = props.initialProjectId || AUTO_PROJECT_ID
    threadCreationRequest.value = null
  }
  if (threadId) {
    restorePendingInterruptForThread(threadId)
  }
})
</script>

<style lang="less" scoped>
.personal-session-bubble {
  display: inline-flex; align-items: center; gap: 8px;
  align-self: flex-start; width: fit-content; max-width: min(100%, 360px); min-height: 40px;
  margin: 6px 0; padding: 9px 12px;
  border: 1px solid var(--border-surface); border-radius: 12px;
  background: var(--gray-25); color: var(--color-text); font: inherit;
  font-size: 13px; line-height: 1.5; text-align: left; cursor: pointer;
  svg, .personal-session-label { flex-shrink: 0; color: var(--color-text-secondary); }
  &:hover { background: var(--gray-50); border-color: var(--border-surface-hover); }
  &:focus-visible { outline: 2px solid var(--main-color); outline-offset: 2px; }
}
.personal-session-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 500; }
.personal-chat-empty { margin: auto; padding: 40px 24px; color: var(--color-text-secondary); text-align: center; }
.personal-chat-main > .chat-box { max-width: 768px; padding: 20px 24px 36px; }
.personal-chat-main > .bottom { padding: 12px 24px 18px; background: var(--bg-page); }
.personal-chat-main > .bottom > .message-input-wrapper { max-width: 720px; }
.personal-chat-main > .chat-box > .group-box { margin-bottom: 12px; }
.personal-chat-main > .chat-box > .group-box > .message-group-time { margin: 20px 0 16px; font-size: 12px; }
.personal-chat-main > .chat-box :deep(.message-box.ai) {
  width: fit-content;
  max-width: 88%;
  margin: 4px 0;
  padding: 12px 16px;
  border-radius: 18px;
  background: var(--gray-50);
  color: var(--color-text);
  line-height: 1.6;
}
.personal-chat-main > .chat-box :deep(.message-box.human) {
  max-width: 80%;
  margin: 4px 0;
  padding: 10px 16px;
  border-radius: 18px;
  background: color-mix(in srgb, var(--main-50) 45%, var(--gray-100));
  line-height: 1.6;
}

.agent-availability-notice {
  position: absolute;
  bottom: calc(100% + 8px);
  left: 0;
  right: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 8px;
  background: var(--gray-0);
  color: var(--gray-600);
  font-size: 14px;
}

@import '@/assets/css/animations.less';
@import '@/modules/session/ui/composerStyles.less';

.chat-container {
  display: flex;
  width: 100%;
  height: 100%;
  position: relative;
}

.chat {
  --header-height: 40px;

  position: relative;
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden; /* Changed from overflow-x: hidden to overflow: hidden */
  position: relative;
  box-sizing: border-box;
  transition: all 0.3s ease;

  .chat-header {
    user-select: none;
    z-index: 10;
    height: var(--header-height);
    min-height: var(--header-height);
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0 8px;
    flex-shrink: 0; /* Prevent header from shrinking */
    transition: padding-right 0.3s cubic-bezier(0.4, 0, 0.2, 1);

    &.has-active-thread {
      border-bottom: 1px solid var(--gray-150);
    }

    .header__left,
    .header__right {
      display: flex;
      align-items: center;
      gap: 8px;
    }

    .switch-icon {
      color: var(--gray-500);
      transition: all 0.2s ease;
    }

    .agent-nav-btn:hover .switch-icon {
      color: var(--main-500);
    }

    .session-title {
      font-size: 14px;
      line-height: 20px;
      font-weight: 400;
      color: var(--gray-900);
      max-width: 200px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      margin-left: 8px;
    }
  }

  &.has-file-panel .chat-header {
    padding-right: calc(var(--file-panel-width) + 8px);
  }

  &.has-maximized-panel .chat-header {
    padding-right: 8px;
  }

  &.is-resizing-file-panel {
    .chat-header,
    .chat-main {
      transition: none;
    }
  }
}

.chat-content-container {
  flex: 1;
  display: flex;
  flex-direction: row;
  overflow: hidden;
  position: relative;
  width: 100%;
  contain: layout;
}

.chat-main {
  flex: 1 1 0;
  display: flex;
  flex-direction: column;
  overflow-y: auto; /* Scroll is here now */
  position: relative;
  transition:
    flex-basis 0.3s cubic-bezier(0.4, 0, 0.2, 1),
    margin-right 0.3s cubic-bezier(0.4, 0, 0.2, 1),
    padding-right 0.24s cubic-bezier(0.16, 1, 0.3, 1),
    width 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  min-width: 0; /* Prevent flex item from overflowing */

  scrollbar-width: none;
  overflow-anchor: none; /* 字符锚点与分页补偿统一拥有位置，避免浏览器二次调整。 */
}

.chat-content-container.has-file-panel .chat-main {
  margin-right: var(--file-panel-width);
}

.chat-main.has-embedded-state {
  box-sizing: border-box;
  padding-right: 364px;
}

.chat-main.has-personal-profile {
  box-sizing: border-box;
  padding-right: 352px;
}

.personal-profile-dock {
  position: absolute;
  top: 60px;
  bottom: 24px;
  width: 320px;
  z-index: 2;
  pointer-events: none;
}

.personal-workspace-actions {
  position: absolute;
  top: 12px;
  z-index: 5;
  display: flex;
  align-items: center;
  gap: 6px;
}

.chat.has-maximized-panel .chat-main {
  margin-right: 0;
  min-width: 0;
}

.side-panel {
  flex: 0 0 auto;
  overflow: hidden;
  background: var(--gray-0);
  border: 1px solid var(--gray-150);
  border-radius: 10px;
  box-shadow:
    0 16px 40px var(--shadow-1),
    0 2px 10px var(--shadow-0);
  z-index: 20;
  min-width: 0;
  opacity: 0;
  pointer-events: none;
  transform: translateX(10px);
  will-change: width, flex-basis, opacity, transform;
  transition:
    width 0.24s cubic-bezier(0.16, 1, 0.3, 1),
    flex-basis 0.24s cubic-bezier(0.16, 1, 0.3, 1),
    opacity 0.22s ease,
    transform 0.24s cubic-bezier(0.16, 1, 0.3, 1);
}

.side-panel.is-visible {
  opacity: 1;
  pointer-events: auto;
  transform: translateX(0);
}

.side-panel.no-transition {
  transition: none !important;
}

.side-panel--file {
  position: absolute;
  top: 0;
  right: 0;
  bottom: 0;
  z-index: 30;
  display: flex;
  height: auto;
  max-width: 100%;
  border: none;
  border-left: 0 solid var(--gray-150);
  border-radius: 0;
  box-shadow: none;
}

.side-panel--file.is-visible {
  min-width: 0;
  border-left-width: 1px;
}

.side-panel--state {
  position: absolute;
  top: calc(var(--header-height) + 8px);
  right: 8px;
  width: min(340px, calc(100% - 24px));
  min-width: 0;
  height: auto;
  margin: 0;
  z-index: 40;
  overflow: hidden;
  box-shadow:
    0 12px 28px var(--shadow-1),
    0 2px 8px var(--shadow-0);
}

.side-panel--state .state-panel {
  height: auto;
  max-height: calc(100vh - 16px);
}

.side-panel--state.is-embedded {
  box-shadow: none;
}

.state-panel {
  width: 100%;
  min-width: 0;
  height: 100%;
  display: flex;
  flex-direction: column;
  background: var(--gray-0);
  flex-shrink: 0;
  box-sizing: border-box;
}

.chat-greeting-input {
  padding: 10px 0;
  text-align: center;
  margin-bottom: 7vh;

  h1 {
    font-size: 1.4rem;
    color: var(--gray-1000);
    margin: 0;
  }
}

.agent-segment-wrapper {
  width: fit-content;
  max-width: 100%;
  margin: 0 auto 18px;
  overflow-x: auto;
  scrollbar-width: none;

  &::-webkit-scrollbar {
    display: none;
  }

  :deep(.ant-segmented) {
    width: auto;
    max-width: 100%;
    white-space: nowrap;
    background: var(--gray-50);
    border: 1px solid var(--gray-150);
    border-radius: 10px;
  }

  :deep(.ant-segmented-group) {
    width: auto;
    display: inline-flex;
  }

  :deep(.ant-segmented-item) {
    flex: 0 0 auto;
    min-width: 0;
  }

  :deep(.ant-segmented-item-label) {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

.agent-switcher-wrapper {
  display: flex;
  justify-content: center;
  margin: 0 auto 18px;
}

.agent-switcher-btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  max-width: 100%;
  padding: 4px 12px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);
  color: var(--gray-900);
  cursor: pointer;
  transition:
    background-color 0.2s ease,
    border-color 0.2s ease,
    color 0.2s ease;

  &:hover {
    background: var(--gray-0);
    border-color: var(--gray-200);
  }
}

.agent-switcher-icon,
.agent-switcher-chevron {
  flex-shrink: 0;
  color: var(--gray-600);
}

.agent-switcher-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

:deep(.agent-switcher-menu) {
  min-width: 220px;
}

:deep(.agent-switcher-menu-item) {
  display: flex;
  align-items: center;
  gap: 8px;
}

:deep(.agent-switcher-menu-icon) {
  flex-shrink: 0;
  color: var(--gray-600);
}

:deep(.agent-switcher-menu-text) {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

:deep(.agent-switcher-menu-badge) {
  flex-shrink: 0;
  padding: 1px 8px;
  border-radius: 999px;
  background: var(--main-30);
  color: var(--main-700);
  font-size: 12px;
}

.session-loading {
  position: absolute;
  top: 30%;
  left: 50%;
  transform: translate(-50%, -50%);
  z-index: 2;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  color: var(--gray-600);
  font-size: 14px;
  line-height: 20px;
  pointer-events: none;

  :deep(.ant-spin) {
    display: flex;
    line-height: 1;
  }
}

.chat-box {
  width: 100%;
  max-width: 800px;
  margin: 0 auto;
  flex-grow: 1;
  padding: 1rem 2rem;
  display: flex;
  flex-direction: column;
}

.group-box,
.history-display-item {
  display: flex;
  flex-direction: column;
}

// 悬浮整个 Run 分组时显示 refs 行中的时间
.group-box:hover {
  :deep(.refs .time-entry) {
    opacity: 1;
    visibility: visible;
  }
}

.message-group-time {
  margin: 24px 0 16px;
  color: var(--color-text-secondary);
  font-size: 13px;
  line-height: 1.6;
  text-align: center;
}

.chat-inline-notice {
  display: flex;
  justify-content: center;
  padding: 6px 16px 12px;
  color: var(--gray-500);
  font-size: 12px;
  line-height: 1.6;
  text-align: center;
}

.bottom {
  position: sticky;
  bottom: 0;
  width: 100%;
  margin: 0 auto;
  padding: 14px 14px 0 14px;
  z-index: 1000;
  background: linear-gradient(to bottom, transparent, var(--bg-page) 10%);

  .message-input-wrapper {
    width: 100%;
    max-width: 800px;
    margin: 0 auto;

    .message-input-stage {
      position: relative;
      min-width: 0;
      z-index: 1;
    }

    .message-input-stage.has-tool-approval {
      display: grid;

      > .approval-modal,
      > .message-input-surface {
        min-width: 0;
        grid-area: 1 / 1;
      }

      > .approval-modal {
        z-index: 2;
      }

      > .message-input-surface {
        opacity: 0;
        pointer-events: none;
      }
    }

    .message-input-surface {
      min-width: 0;
      transition: opacity 0.18s ease;
    }

    .queued-request-panel {
      .composer-top-attachment();
      max-height: 196px;
      overflow-y: auto;
      padding: 6px 14px 4px;
    }

    .queued-request-notice {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin: 0 6px 4px;
      padding: 0;
      color: var(--color-text-tertiary);
      background: transparent;
      font-size: 13px;
      line-height: 1.5;

      &.is-paused {
        color: var(--color-warning-700);
        background: transparent;
      }
    }

    .queued-request-continue {
      display: inline-flex;
      flex: 0 0 auto;
      align-items: center;
      gap: 4px;
      padding: 4px 0;
      color: var(--color-warning-700);
      background: transparent;
      border: 0;
      cursor: pointer;
      font-size: 12px;

      &:disabled {
        opacity: 0.55;
        cursor: wait;
      }
    }

    .queued-request-list {
      display: flex;
      flex-direction: column;
      gap: 2px;
    }

    .queued-request-row {
      min-height: 28px;
      display: grid;
      grid-template-columns: 18px minmax(0, 1fr) auto auto;
      gap: 10px;
      align-items: center;
      padding: 0 4px 0 6px;
      color: var(--color-text);
      border-radius: 8px;
      transition: background-color 0.18s ease;

      &:hover {
        background: var(--gray-50);
      }
    }

    .queued-request-icon {
      color: var(--gray-500);
    }

    .queued-request-content {
      min-width: 0;
      overflow: hidden;
      font-size: 13px;
      font-weight: 400;
      line-height: 1.4;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .queued-request-position {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      color: var(--gray-500);
      font-size: 12px;
      font-variant-numeric: tabular-nums;
      white-space: nowrap;

      &::before {
        content: '↪';
        color: var(--gray-400);
        font-size: 14px;
      }
    }

    .queued-request-actions {
      display: inline-flex;
      align-items: center;
      justify-content: flex-end;
      min-width: 64px;
      gap: 4px;
    }

    .queued-request-status {
      color: var(--color-text-tertiary);
      font-size: 12px;
      white-space: nowrap;
    }

    .queued-request-steer,
    .direct-steer-button {
      height: 28px;
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 0 6px;
      color: var(--gray-500);
      background: transparent;
      border: 0;
      border-radius: 6px;
      cursor: pointer;
      font-size: 12px;
      line-height: 1;
      transition:
        color 0.18s ease,
        background-color 0.18s ease;

      &:hover:not(:disabled) {
        color: var(--gray-700);
        background: var(--gray-100);
      }

      &:focus-visible {
        outline: 2px solid var(--main-color);
        outline-offset: 1px;
      }

      &:disabled {
        opacity: 0.45;
        cursor: wait;
      }
    }

    .queued-request-delete {
      width: 30px;
      height: 30px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 0;
      color: var(--gray-500);
      background: transparent;
      border: 0;
      border-radius: 6px;
      cursor: pointer;
      transition:
        color 0.18s ease,
        background-color 0.18s ease;

      &:hover:not(:disabled) {
        color: var(--color-error-700);
        background: var(--color-error-50);
      }

      &:focus-visible {
        outline: 2px solid var(--main-color);
        outline-offset: 1px;
      }

      &:disabled {
        color: var(--gray-300);
        cursor: wait;
      }
    }

    .queued-request-promote {
      width: 30px;
      height: 30px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 0;
      color: var(--gray-500);
      background: transparent;
      border: 0;
      border-radius: 6px;
      cursor: pointer;
      transition:
        color 0.18s ease,
        background-color 0.18s ease;

      &:hover:not(:disabled) {
        color: var(--color-success-700);
        background: var(--color-success-50);
      }

      &:focus-visible {
        outline: 2px solid var(--main-color);
        outline-offset: 1px;
      }

      &:disabled {
        color: var(--gray-300);
        cursor: wait;
      }
    }

    .bottom-actions {
      display: flex;
      justify-content: center;
      align-items: center;
      width: 100%;
    }

    .note {
      font-size: small;
      color: var(--gray-300);
      margin: 4px 0;
      user-select: none;
    }
  }

  .input-model-selector {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-width: 0;
    max-width: min(168px, calc(100vw - 160px));
  }

  &.start-screen {
    position: absolute;
    top: 45%;
    left: 50%;
    transform: translate(-50%, -50%);
    bottom: auto;
    max-width: 800px;
    width: 90%;
    background: transparent;
    padding: 0;
    border-top: none;
    z-index: 100; /* Ensure it's above other elements */
  }
}

.loading-spark {
  position: relative;
  display: inline-block;
  flex: 0 0 22px;
  width: 22px;
  height: 22px;
  color: var(--main-color);
}

.loading-spark::before,
.loading-spark::after {
  content: '';
  position: absolute;
  background: currentColor;
}

.loading-spark::before {
  inset: 5%;
  clip-path: polygon(50% 0, 63% 36%, 100% 50%, 63% 64%, 50% 100%, 37% 64%, 0 50%, 37% 36%);
  animation: loading-spark-bloom 2.6s ease-in-out infinite;
}

.loading-spark::after {
  top: 0;
  right: 0;
  width: 24%;
  height: 24%;
  clip-path: polygon(50% 0, 65% 35%, 100% 50%, 65% 65%, 50% 100%, 35% 65%, 0 50%, 35% 35%);
  animation: loading-spark-satellite 2.6s ease-in-out infinite;
}

@keyframes loading-spark-bloom {
  0%,
  100% {
    transform: scale(0.65) rotate(-15deg);
    opacity: 0.6;
  }
  50% {
    transform: scale(1) rotate(75deg);
    opacity: 1;
  }
}

@keyframes loading-spark-satellite {
  0%,
  100% {
    transform: scale(0.3);
    opacity: 0.2;
  }
  50% {
    transform: scale(1);
    opacity: 1;
  }
}

@media (prefers-reduced-motion: reduce) {
  .loading-spark::before,
  .loading-spark::after {
    animation: none;
  }
}

.generating-status {
  display: flex;
  justify-content: flex-start;
  padding: 1rem 0;
}

.generating-indicator {
  display: flex;
  align-items: center;
  gap: 8px;

  // 轻微呼吸的文本，替代原先的高亮闪动
  .generating-text {
    font-size: 14px;
    font-weight: 500;
    letter-spacing: 0.025em;
    color: var(--gray-600);
    animation: textBreath 1.8s ease-in-out infinite;
  }
}

@keyframes textBreath {
  0%,
  100% {
    opacity: 0.55;
  }
  50% {
    opacity: 1;
  }
}

.generating-cancel {
  border: none;
  padding: 4px;
  background: transparent;
  color: var(--gray-500);

  &:disabled {
    cursor: wait;
  }
}

@media (max-width: 1024px) {
  .chat-content-container.has-file-panel .chat-main {
    min-width: 350px;
  }

  .side-panel--file.is-visible {
    max-width: 100%;
  }
}

@media (max-width: 768px) {
  .chat.has-file-panel .chat-header {
    padding-right: 8px;
  }

  .chat-content-container.has-file-panel .chat-main {
    margin-right: 0;
    min-width: 0;
  }

  .side-panel--file {
    top: calc(var(--header-height) + 4px);
  }

  .side-panel--file.is-visible {
    min-width: 0;
    max-width: calc(100% - 16px);
  }

  .side-panel--state.is-visible {
    min-width: 0;
    max-width: calc(100% - 24px);
  }

  .chat.has-maximized-panel .side-panel--file {
    top: 0;
    max-width: 100%;
  }

  .state-panel {
    width: 100%;
    min-width: 0;
  }

  .agent-segment-wrapper {
    margin-bottom: 8px;

    :deep(.ant-segmented-item-label) {
      font-size: 12px;
    }
  }

  .agent-switcher-wrapper {
    margin-bottom: 8px;
  }

  .agent-switcher-btn {
    width: 100%;
    justify-content: center;
  }

  .chat-header {
    .header__left {
      .text {
        display: none;
      }
    }
  }
}

// 智能体选择器的图标对齐
.agent-segment-wrapper {
  :deep(.ant-segmented-item-label) {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  :deep(.agent-option-label) {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  :deep(.agent-option-icon) {
    flex-shrink: 0;
    color: var(--gray-600);
  }
}
</style>

<style lang="less">
.agent-nav-btn {
  display: flex;
  gap: 5px;
  padding: 4px 7px;
  height: 28px;
  justify-content: center;
  align-items: center;
  border-radius: 6px;
  color: var(--gray-900);
  cursor: pointer;
  width: auto;
  font-size: 14px;
  line-height: 20px;
  transition: background-color 0.3s;
  border: none;
  background: transparent;

  &:hover:not(.is-disabled) {
    background-color: var(--gray-100);
  }

  &.is-disabled {
    cursor: not-allowed;
    opacity: 0.7;
    pointer-events: none;
  }

  .nav-btn-icon {
    width: 16px;
    height: 16px;
  }

  .loading-icon {
    animation: spin 1s linear infinite;
  }
}

.history-load-btn {
  min-height: 40px;
  flex-shrink: 0;
  color: var(--color-text-secondary);

  &:disabled {
    cursor: wait;
  }

  &:focus-visible {
    outline: 2px solid var(--main-color);
    outline-offset: 2px;
  }
}

.side-panel__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-height: var(--header-height);
  padding: 4px 12px;
  background: var(--gray-25);
  border-bottom: 1px solid var(--gray-100);
  flex-shrink: 0;
}

.state-entry-btn.active {
  color: var(--main-700);
  background-color: var(--main-20);
}

.state-panel-header {
  padding: 10px 14px;
  padding-bottom: 0px;
  background: transparent;
  border-bottom: none;
}

.state-refresh-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  padding: 0;
  border: none;
  border-radius: 6px;
  color: var(--gray-500);
  background: transparent;
  cursor: pointer;
  opacity: 0;
  pointer-events: none;
  transition:
    opacity 0.16s ease,
    color 0.16s ease,
    background-color 0.16s ease;

  &:hover:not(:disabled) {
    color: var(--main-700);
    background: var(--gray-100);
  }

  &:disabled {
    cursor: not-allowed;
  }

  .is-spinning {
    animation: spin 1s linear infinite;
  }
}

.state-panel:hover .state-refresh-btn,
.state-panel:focus-within .state-refresh-btn,
.state-refresh-btn:focus-visible {
  opacity: 1;
  pointer-events: auto;
}

@media (hover: none) {
  .state-refresh-btn {
    opacity: 1;
    pointer-events: auto;
  }
}

.state-panel-title {
  min-width: 0;
  font-size: 14px;
  font-weight: 400;
  color: var(--gray-500);
}

.state-section-meta {
  flex-shrink: 0;
  font-size: 12px;
  color: var(--gray-500);
}

.state-panel-body {
  flex: 1;
  min-height: 0;
  padding: 8px 14px 14px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  overflow: auto;

  // 子项被 flex 压缩时会溢出贴底、吃掉 padding，空间不足应交给滚动
  > * {
    flex-shrink: 0;
  }
}

.state-warning {
  padding: 10px 12px;
  border-radius: 6px;
  background: var(--color-warning-50);
  color: var(--color-warning-900);

  p {
    margin: 4px 0 0;
    font-size: 13px;
    line-height: 1.6;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
}

.state-section {
  display: flex;
  flex-direction: column;

  .state-collapse-panel.is-expanded {
    margin-top: 6px;
  }
}

.state-collapse-panel {
  display: grid;
  grid-template-rows: 0fr;
  transition:
    grid-template-rows 0.24s cubic-bezier(0.16, 1, 0.3, 1),
    visibility 0.24s ease;
  visibility: hidden;
  min-width: 0;

  &.is-expanded {
    grid-template-rows: 1fr;
    visibility: visible;
  }
}

.state-collapse-inner {
  overflow: hidden;
  min-height: 0;
  opacity: 0;
  transform: translateY(-4px);
  transition:
    opacity 0.2s ease,
    transform 0.2s cubic-bezier(0.16, 1, 0.3, 1);
}

.state-collapse-panel.is-expanded .state-collapse-inner {
  opacity: 1;
  transform: translateY(0);
}

.state-section-header {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 4px 0;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;

  &:hover {
    .state-section-title,
    .state-section-chevron {
      color: var(--gray-900);
    }
  }

  &:focus-visible {
    outline: 2px solid var(--main-200);
    outline-offset: 2px;
  }
}

.state-section-label {
  min-width: 0;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.state-section-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--gray-800);
}

.state-section-chevron {
  flex-shrink: 0;
  color: var(--gray-400);
  transition:
    transform 0.22s cubic-bezier(0.16, 1, 0.3, 1),
    color 0.18s ease;

  &.is-collapsed {
    transform: rotate(-90deg);
  }
}

.state-section-content {
  min-width: 0;
}

.state-panel-empty {
  padding: 10px 12px;
  border-radius: 10px;
  background: var(--gray-25);
  color: var(--gray-500);
  font-size: 13px;
  text-align: center;
}

@media (prefers-reduced-motion: reduce) {
  .state-section-chevron,
  .state-collapse-panel,
  .state-collapse-inner {
    transition: none;
  }
}

.state-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.state-list-item {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 7px 9px;
  border: 1px solid var(--gray-100);
  border-radius: 10px;
  background: var(--gray-25);
  color: inherit;
  text-align: left;
}

.state-list-item--button {
  cursor: pointer;
}

.state-list-item--button:hover,
.state-list-item.is-clickable:hover {
  border-color: var(--main-200);
  background: var(--gray-0);
}

.state-list-item.is-clickable {
  cursor: pointer;
}

.state-list-item--file,
.state-list-item--artifact {
  min-height: 32px;
  padding: 5px 8px;
}

.state-list-item-icon {
  width: 15px;
  height: 15px;
  flex-shrink: 0;
  font-size: 15px;
}

.state-list-item-body {
  min-width: 0;
  flex: 1;
}

.state-list-item-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--gray-900);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.state-list-item-meta {
  margin-top: 1px;
  font-size: 12px;
  line-height: 1.25;
  color: var(--gray-500);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.hide-text {
  display: none;
}

@media (min-width: 769px) {
  .hide-text {
    display: inline;
  }
}

/* AgentState 按钮有内容时的样式 */
.agent-nav-btn.agent-state-btn.has-content:hover:not(.is-disabled) {
  color: var(--gray-900);
  background-color: var(--gray-100);
}

.agent-nav-btn.agent-state-btn.active {
  color: var(--gray-900);
  background-color: var(--gray-100);
}

.agent-nav-btn.agent-debug-mode-btn {
  color: var(--gray-800);
  background-color: var(--gray-100);
  border: 1px solid var(--gray-300);

  &:hover {
    background-color: var(--gray-200);
    color: var(--gray-1000);
    border-color: var(--gray-400);
  }

  .debug-icon {
    color: var(--gray-700);
  }
}

.panel-main-chat,
.panel-session-chat {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.panel-main-chat :deep(.chat-main) {
  margin-right: 0;
  height: 100%;
}
.panel-session-chat :deep(.chat-container) {
  height: 100%;
  min-height: 0;
}
.embedded-session {
  height: 100%;
  min-height: 0;
}

.embedded-session .chat-main {
  margin-right: 0;
}

.embedded-session .bottom {
  background: linear-gradient(to bottom, transparent, var(--gray-0) 10%);
}

.chat > .side-panel--state.is-floating {
  position: absolute;
  z-index: 40;
}
</style>
