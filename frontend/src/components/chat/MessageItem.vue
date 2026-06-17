<template>
  <div class="message-row" :class="row_class">
    <el-dropdown trigger="contextmenu" @command="handle_command">
      <div class="message-bubble">
        <span class="role-label">{{ role_label }}</span>
        <p v-if="message.content" class="message-text">{{ message.content }}</p>
        <el-image
          v-if="message.image_url"
          class="message-image"
          :src="message.image_url"
          :preview-src-list="[message.image_url]"
          fit="cover"
          preview-teleported
        />
      </div>
      <template #dropdown>
        <el-dropdown-menu>
          <el-dropdown-item command="recall">撤回</el-dropdown-item>
        </el-dropdown-menu>
      </template>
    </el-dropdown>
  </div>
</template>

<script setup>
import { computed } from 'vue'

// ---------- 组件输入：单条消息 ----------
const props = defineProps({
  message: {
    type: Object,
    required: true
  }
})

// ---------- 组件输出：撤回等操作 ----------
const emit = defineEmits(['recall'])

// ---------- 样式状态：根据角色决定消息左右对齐 ----------
const row_class = computed(() => {
  return props.message.role === 'merchant' ? 'is-merchant' : 'is-buyer'
})

// ---------- 展示文本：角色标签中文化 ----------
const role_label = computed(() => {
  return props.message.role === 'merchant' ? '商家' : '买家'
})

// ---------- 右键菜单：分发撤回事件 ----------
function handle_command(command) {
  if (command === 'recall') {
    emit('recall', props.message.id)
  }
}
</script>

<style scoped>
.message-row {
  display: flex;
  margin-bottom: 12px;
}

.message-row.is-buyer {
  justify-content: flex-start;
}

.message-row.is-merchant {
  justify-content: flex-end;
}

.message-row :deep(.el-dropdown) {
  display: flex;
  max-width: 80%;
  min-width: 0;
}

.message-row.is-merchant :deep(.el-dropdown) {
  justify-content: flex-end;
}

.message-bubble {
  max-width: 100%;
  min-width: 0;
  border-radius: 8px;
  padding: 10px 12px;
  background-color: #f2f6fc;
  box-sizing: border-box;
}

.message-row.is-merchant .message-bubble {
  background-color: #ecf5ff;
}

.role-label {
  display: inline-block;
  font-size: 12px;
  color: #606266;
  margin-bottom: 6px;
}

.message-text {
  margin: 0;
  color: #303133;
  line-height: 1.5;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  word-break: break-word;
}

.message-image {
  margin-top: 8px;
  width: 180px;
  max-width: 100%;
  border-radius: 6px;
}
</style>
