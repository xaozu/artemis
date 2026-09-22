/**
 * Copyright 2026 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

import { Injectable, signal } from '@angular/core';
import { TuningLevel } from '../models/pro-tuning.model';

export type UiLanguage = 'en' | 'zh';

/** First-pass translations for the task entry and execution controls. */
const UI_TEXT = {
  en: {
    'nav.main': 'Main navigation',
    'nav.home': 'New / Home',
    'nav.homeTitle': 'Return home to start a new task',
    'nav.workspace': 'Workspace',
    'nav.workspaceTitle': 'Open workspace',
    'nav.workflows': 'Workflows',
    'nav.workflowsTitle': 'Record and replay physical phone actions',
    'nav.switchToChinese': 'Switch to Chinese',
    'nav.switchToEnglish': '切换为英文',
    'home.hero': 'What would you like to do on your phone?',
    'home.customWorkflow': 'Record workflow',
    'home.setupTab': 'System Setup & Prerequisites',
    'home.launchTab': 'Launch Autonomous Task',
    'home.setupTitle': 'System & Environment',
    'home.setupDescription': 'Core tools, Android debug bridge, and video recording runtime',
    'home.recheck': 'Re-check',
    'home.checking': 'Checking...',
    'home.recheckTitle': 'Re-check all system dependencies',
    'home.readyTitle': 'System Ready for Autonomous Tasks',
    'home.readyDescription': 'All dependencies, AI model credentials, and Android device are verified and ready to run.',
    'home.lockedWarning': 'Android device is locked. Unlock it and enter the home screen before running a task.',
    'home.setupWarning': 'Prerequisites incomplete: runtime, credentials, or Android device is not ready. Click here to open Setup Guide.',
    'home.proSuggestion': 'Continuous monitoring or complex task detected. Recommended:',
    'home.proMode': 'Pro Mode',
    'home.switchToPro': 'Switch to Pro',
    'home.goalPlaceholder': 'Describe what you want to achieve on your phone (e.g. Open Gmail and search for latest emails)...',
    'home.flashDescription': 'Token-efficient, rapid 3–5s/step decisions straight from the live screen. Best for everyday tasks with a clear path, however long they run.',
    'home.flashLimitation': '(No plan, no result check, no ADB commands)',
    'home.proDescription': 'Plans first, checks its results and recovers when a tap is blocked. Handles 100+ step workflows and live phone monitoring (15–40s / step).',
    'home.flashTitle': 'Flash: fast single-look loop for everyday app tasks (3–5s/step)',
    'home.proTitle': 'Pro: plans first, checks results and recovers from blocked steps. For long or complex tasks (15–40s/step)',
    'home.chars': 'chars',
    'home.unlockTitle': 'Unlock the Android device before running',
    'home.completeSetupTitle': 'Complete System Setup before running',
    'home.runTitle': 'Run task',
    'home.starting': 'Starting...',
    'home.runTask': 'Run Task',
    'home.expectedOutput': 'Expected Output',
    'home.optional': '(Optional)',
    'home.expectedOutputPlaceholder': "Specify expected output or schema (e.g. 'Extract SMS verification code as JSON' or 'Summary of discounts and total price')...",
    'home.clearExpectedOutput': 'Clear expected output',
    'home.proSettings': 'Pro settings',
    'home.reset': 'Reset',
    'home.resetTitle': 'Back to the defaults',
    'home.resultCheck': 'Result check',
    'home.screenReading': 'Screen reading',
    'home.faster': 'Faster',
    'home.sharper': 'Sharper',
    'home.whatHappens': 'What happens',
    'home.notIncluded': 'Not included',
    'home.useItFor': 'Use it for',
    'home.enterGoalError': 'Please enter a task goal before running.',
    'home.prerequisitesError': 'System prerequisites are not satisfied. Please review System Setup first.',
    'home.submitError': 'Failed to submit task. Please check server connection.',
    'workspace.newTask': 'Start a new task...',
    'workspace.proPlaceholder': 'Pro: plans first, checks results and recovers from blocked steps. For long or complex tasks',
    'workspace.flashPlaceholder': 'Flash: fast single-look loop for everyday app tasks',
    'workspace.closeError': 'Close error message',
    'workspace.clearInput': 'Clear input text',
    'workspace.startingTitle': 'Starting task...',
    'workspace.startTitle': 'Start task (Enter)',
    'workspace.startTask': 'Start task',
    'workspace.profileGroup': 'Select task mode',
    'workspace.stopCurrent': 'Stop current task',
    'workspace.stopRunning': 'Stop current running task',
    'workspace.busyError': 'The runner is busy. Please wait for current task to finish.',
    'tasks.queue': 'Task Queue',
    'tasks.notes': 'Notes & Plans',
    'tasks.active': 'Active Queue',
    'tasks.history': 'Past Runs & History',
    'tasks.empty': 'No active or queued tasks.',
    'tasks.stop': 'Stop this task',
    'tasks.delete': 'Delete this task',
    'tasks.clearHistory': 'Clear all tasks and history',
    'tasks.selectToView': 'Please select a session from the Task List on the right to view its real-time log payloads.',
    'tasks.completed': 'Task Completed',
    'tasks.failed': 'Task Execution Failed',
    'tasks.report': 'Task Report'
  },
  zh: {
    'nav.main': '主导航',
    'nav.home': '新任务 / 首页',
    'nav.homeTitle': '返回首页创建新任务',
    'nav.workspace': '工作台',
    'nav.workspaceTitle': '打开工作台',
    'nav.workflows': '步骤模板',
    'nav.workflowsTitle': '录制实体手机操作并回放',
    'nav.switchToChinese': 'Switch to Chinese',
    'nav.switchToEnglish': '切换为英文',
    'home.hero': '你想让手机完成什么任务？',
    'home.customWorkflow': '录制步骤模板',
    'home.setupTab': '环境检查与准备',
    'home.launchTab': '启动自动化任务',
    'home.setupTitle': '系统与环境',
    'home.setupDescription': '检查基础工具、ADB 和录屏组件',
    'home.recheck': '重新检查',
    'home.checking': '检查中...',
    'home.recheckTitle': '重新检查所有系统依赖',
    'home.readyTitle': '系统已准备好运行自动化任务',
    'home.readyDescription': '依赖、模型凭证和 Android 设备均已检查通过。',
    'home.lockedWarning': 'Android 设备已锁定，请解锁并进入主屏幕后再运行任务。',
    'home.setupWarning': '运行环境、模型凭证或设备尚未准备好。点击查看环境准备说明。',
    'home.proSuggestion': '检测到持续监控或复杂任务，建议使用：',
    'home.proMode': 'Pro 模式',
    'home.switchToPro': '切换到 Pro',
    'home.goalPlaceholder': '描述你希望手机完成的任务，例如：打开 Gmail，查找最新邮件...',
    'home.flashDescription': '节省模型用量，每步约 3–5 秒，直接根据当前画面决策。适合路径清晰的日常任务，即使任务较长也可使用。',
    'home.flashLimitation': '（不规划、不核验结果，也不能执行 ADB 命令）',
    'home.proDescription': '先规划并核验结果，点击受阻时可尝试恢复。支持 100 步以上流程及持续监控，每步约 15–40 秒。',
    'home.flashTitle': 'Flash：快速查看画面并执行日常任务（每步约 3–5 秒）',
    'home.proTitle': 'Pro：先规划、再核验，适合较长或复杂的任务（每步约 15–40 秒）',
    'home.chars': '字',
    'home.unlockTitle': '请先解锁 Android 设备',
    'home.completeSetupTitle': '请先完成环境准备',
    'home.runTitle': '运行任务',
    'home.starting': '启动中...',
    'home.runTask': '运行任务',
    'home.expectedOutput': '预期输出',
    'home.optional': '（可选）',
    'home.expectedOutputPlaceholder': '描述希望得到的结果或格式，例如：以 JSON 返回短信验证码...',
    'home.clearExpectedOutput': '清空预期输出',
    'home.proSettings': 'Pro 设置',
    'home.reset': '重置',
    'home.resetTitle': '恢复默认设置',
    'home.resultCheck': '结果核验',
    'home.screenReading': '画面识别',
    'home.faster': '更快',
    'home.sharper': '更细致',
    'home.whatHappens': '执行方式',
    'home.notIncluded': '不包含',
    'home.useItFor': '适用场景',
    'home.enterGoalError': '请先输入任务目标。',
    'home.prerequisitesError': '运行条件尚未满足，请先查看环境准备。',
    'home.submitError': '任务提交失败，请检查服务连接。',
    'workspace.newTask': '输入新任务...',
    'workspace.proPlaceholder': 'Pro：先规划并核验结果，遇到受阻步骤会尝试恢复，适合复杂任务',
    'workspace.flashPlaceholder': 'Flash：快速查看画面并执行日常任务',
    'workspace.closeError': '关闭错误提示',
    'workspace.clearInput': '清空输入',
    'workspace.startingTitle': '正在启动任务...',
    'workspace.startTitle': '启动任务（回车）',
    'workspace.startTask': '启动任务',
    'workspace.profileGroup': '选择任务模式',
    'workspace.stopCurrent': '停止当前任务',
    'workspace.stopRunning': '停止正在运行的任务',
    'workspace.busyError': '任务执行器正忙，请等待当前任务结束。',
    'tasks.queue': '任务列表',
    'tasks.notes': '记录与计划',
    'tasks.active': '运行中的任务',
    'tasks.history': '历史任务',
    'tasks.empty': '没有正在运行或排队的任务。',
    'tasks.stop': '停止此任务',
    'tasks.delete': '删除此任务',
    'tasks.clearHistory': '清空所有任务和历史记录',
    'tasks.selectToView': '选择任务以查看执行详情。',
    'tasks.completed': '任务已完成',
    'tasks.failed': '任务执行失败',
    'tasks.report': '任务报告'
  }
} as const;

export type UiTextKey = keyof typeof UI_TEXT.en;

@Injectable({ providedIn: 'root' })
export class UiLanguageService {
  private readonly storageKey = 'artemis_ui_language';
  /** 当前界面语言；只影响网页文案，不改变任务目标或后端参数。 */
  readonly language = signal<UiLanguage>(this.readSavedLanguage());

  constructor() {
    this.updateDocumentLanguage(this.language());
  }

  /** 返回指定界面文案，Angular 模板读取时会跟踪 language 信号。 */
  text(key: UiTextKey): string {
    return UI_TEXT[this.language()][key];
  }

  /** 切换语言并在刷新后恢复用户选择。 */
  setLanguage(language: UiLanguage): void {
    this.language.set(language);
    this.updateDocumentLanguage(language);
    try {
      localStorage.setItem(this.storageKey, language);
    } catch {
      // 禁用本地存储时仍允许本次页面会话切换语言。
    }
  }

  private readSavedLanguage(): UiLanguage {
    try {
      return localStorage.getItem(this.storageKey) === 'zh' ? 'zh' : 'en';
    } catch {
      return 'en';
    }
  }

  private updateDocumentLanguage(language: UiLanguage): void {
    if (typeof document !== 'undefined') {
      document.documentElement.lang = language === 'zh' ? 'zh-CN' : 'en';
    }
  }

  /** 仅翻译滑块展示文案，原始 id 继续用于后端配置。 */
  tuningLevel(kind: 'verify' | 'explore', level: TuningLevel): TuningLevel {
    if (this.language() === 'en') return level;
    return { ...level, ...TUNING_ZH[`${kind}.${level.id}`] };
  }
}

const TUNING_ZH: Record<string, Partial<TuningLevel>> = {
  'verify.off': {
    label: '关闭', tagline: '不核验结果，任务看起来完成后就结束。', latency: '不增加时间',
    runs: ['执行动作后立即进入下一步。', '仍会保留完整操作记录，但不提供通过或失败结论。'],
    skips: ['不复查，也不自动重试。'], bestFor: '快速试用或演示。'
  },
  'verify.final': {
    label: '结束时', tagline: '任务结束时按目标核验一次；这是默认选项。', latency: '结束时约增加 20–60 秒',
    runs: ['对照目标检查最终画面、步骤记录和设备状态。', '结果不符时最多尝试修复 3 次。'],
    skips: ['任务运行过程中不逐步核验。'], bestFor: '日常任务的结果确认。'
  },
  'verify.checkpoints': {
    label: '每步核验', tagline: '每步完成后核验，结束时再做最终检查。', latency: '每步后增加一次后台检查',
    runs: ['根据当时的截图检查每一步。', '步骤出错时最多重试 2 次。', '记录失败的检查项，并在结束时再次核验。'],
    bestFor: '前一步错误会影响后续步骤的长任务。'
  },
  'verify.strict': {
    label: '严格', tagline: '逐步核验；首次检查失败时停止任务。', latency: '最慢，包含更多核验与重试',
    runs: ['每步最多修复 4 次，结束时最多修复 5 次。', '首次检查失败时停止并保留证据。'],
    bestFor: '发布前检查和回归验收。'
  },
  'explore.flash': {
    label: '快速查看', tagline: '查看一次画面，定位按钮和文字。', latency: '每次搜索查看 1 次',
    runs: ['按名称、图标或颜色查找画面元素。', '可以一次查找多个目标。'],
    skips: ['不放大局部，也不二次尝试。'], bestFor: '按钮和文字清晰的常规页面。'
  },
  'explore.pro': {
    label: '再次查看', tagline: '最多查看 3 次，并在每次之间调整判断。', latency: '每次搜索最多查看 3 次',
    runs: ['先读取页面结构，再查看截图。', '首次未找到时尝试其他查找方式。'],
    skips: ['仍不放大局部画面。'], bestFor: '标签不明确或依赖相对位置描述的目标。'
  },
  'explore.ultra': {
    label: '放大查看', tagline: '放大局部画面，最多查看 8 次。', latency: '每次搜索最多查看 8 次',
    runs: ['裁剪并放大局部，逐块读取小字和密集布局。', '后续查看会复用已有结果。'],
    bestFor: '拥挤页面、小目标、图表和精确位置核对。'
  }
};
