<template>
  <div class="dispute-layout">
    <section class="chat-column">
      <ChatPanel
        :messages="messages"
        :input_text="input_text"
        :sender_role="sender_role"
        :pending_images="pending_images"
        :loading="loading"
        :emotion_checking="emotion_checking"
        @update:input_text="update_input_text"
        @update:sender_role="update_sender_role"
        @send_message="send_message"
        @add_pending_image="add_pending_image"
        @remove_pending_image="remove_pending_image"
        @recall_message="recall_message"
        @request_ai_help="request_ai_help"
        @end_processing="show_close_dialog = true"
      />
      <SellerEmotionDialog
        v-model="show_seller_emotion_dialog"
        :mode="emotion_dialog_mode"
        :emotion_alert="seller_emotion_alert"
        @cancel="cancel_emotion_block"
        @confirm_send="confirm_send_despite_emotion"
        @dismiss="dismiss_emotion_notice"
      />
      <CloseDisputeDialog v-model="show_close_dialog" />
    </section>

    <section class="strategy-column">
      <el-alert
        v-if="error_message"
        type="error"
        :title="error_message"
        :closable="false"
        show-icon
        class="error-alert"
      />
      <el-alert
        v-else-if="loading && progress_message"
        type="info"
        :title="progress_message"
        :closable="false"
        show-icon
        class="progress-alert"
      />
      <StrategyPanel
        :report="report"
        :loading="loading"
        :seller_emotion_alert="seller_emotion_alert"
        @use_script="apply_script"
      />
    </section>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import ChatPanel from '../components/chat/ChatPanel.vue'
import StrategyPanel from '../components/strategy/StrategyPanel.vue'
import SellerEmotionDialog from '../components/strategy/SellerEmotionDialog.vue'
import CloseDisputeDialog from '../components/strategy/CloseDisputeDialog.vue'
import { use_dispute } from '../composables/useDispute'

const show_close_dialog = ref(false)

const {
  messages,
  report,
  loading,
  input_text,
  sender_role,
  error_message,
  progress_message,
  pending_images,
  seller_emotion_alert,
  show_seller_emotion_dialog,
  emotion_dialog_mode,
  emotion_checking,
  send_message,
  add_pending_image,
  remove_pending_image,
  recall_message,
  apply_script,
  request_ai_help,
  cancel_emotion_block,
  confirm_send_despite_emotion,
  dismiss_emotion_notice
} = use_dispute()

function update_input_text(value) {
  input_text.value = value
}

function update_sender_role(value) {
  sender_role.value = value
}
</script>

<style scoped>
.dispute-layout {
  height: 100%;
  width: 100%;
  display: grid;
  grid-template-columns: 11fr 9fr;
  gap: 0;
  background: linear-gradient(180deg, #f8fafd 0%, #f1f6fb 55%, #eaf1f8 100%);
}

.chat-column,
.strategy-column {
  min-height: 0;
  min-width: 0;
  height: 100%;
  padding: 12px 14px;
  box-sizing: border-box;
  background: transparent;
}

.strategy-column {
  display: flex;
  flex-direction: column;
}

.error-alert {
  margin-bottom: 10px;
}

.progress-alert {
  margin-bottom: 10px;
}
</style>
