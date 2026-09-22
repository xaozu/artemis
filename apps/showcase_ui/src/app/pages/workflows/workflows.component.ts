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

import { ChangeDetectionStrategy, Component, computed, inject, OnDestroy, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { firstValueFrom } from 'rxjs';
import {
  ManualWorkflowService, PhysicalRecording, WorkflowDevice, WorkflowRun,
  WorkflowStep, WorkflowStepType, WorkflowTemplate
} from '../../core/services/manual-workflow.service';
import { UiLanguageService } from '../../core/services/ui-language.service';

const TEXT = {
  zh: {
    title: '实体手机操作录制', intro: '在手机上操作一遍，保存为可重复执行的步骤。搜索词等输入内容可以设为参数。',
    device: '目标设备', refresh: '刷新设备', start: '开始录制', stop: '停止录制', recording: '正在记录手机触摸…',
    instructions: '直接在手机屏幕操作。停止后，选中输入法敲字对应的连续点击，替换成“输入文本”步骤。',
    steps: '操作步骤', empty: '还没有步骤。', delay: '前置等待（毫秒）', duration: '持续时间（毫秒）',
    text: '文本', parameter: '参数名', clear: '先清空输入框', replace: '将所选连续步骤替换为输入参数',
    replacementText: '录制时输入的文本或默认值', replacementParameter: '参数名，例如 keyword',
    addWait: '添加等待', addAssert: '添加文字验收', addText: '添加输入文本', save: '保存步骤模板', name: '模板名称',
    library: '我的步骤模板', run: '填写参数并回放', delete: '删除模板', wait: '等待', assert: '验收文字',
    selectHint: '勾选连续的键盘点击步骤，再填写文本与参数名。保存后可在每次回放时改搜索词。',
    replay: '开始回放', status: '执行状态', completed: '已完成', failed: '执行失败', running: '执行中',
    saved: '模板已保存。', missingDevice: '请先选择已连接的设备。', missingName: '请填写模板名称。',
    missingSteps: '至少需要一个步骤。', missingSelection: '请勾选连续的点击步骤。',
    invalidParameter: '参数名须以英文字母开头，仅包含字母、数字和下划线。',
    deleteConfirm: '确定删除这个步骤模板吗？', recordLost: '录制已结束，可以继续编辑并保存。',
    remove: '删除', up: '上移', down: '下移', select: '选中', physicalNote: '录制只读取实体触摸。输入法按键会记为点击，请在保存前替换为文本步骤；密码不要录制。',
    recorded: '已录制', tap: '点击', long_press: '长按', swipe: '滑动', back: '返回', home: '主页', textStep: '输入文本',
    noTemplates: '还没有已保存的模板。', refreshTemplates: '刷新模板', saveFirst: '请先保存模板。', discard: '放弃本次录制', cancel: '停止回放', cancelled: '已停止'
  },
  en: {
    title: 'Record Physical Phone Actions', intro: 'Perform a flow on the phone once, then save its actions as a reusable workflow with editable text parameters.',
    device: 'Target device', refresh: 'Refresh devices', start: 'Start recording', stop: 'Stop recording', recording: 'Recording physical touches…',
    instructions: 'Use the phone directly. After stopping, select consecutive keyboard taps and replace them with a text input step.',
    steps: 'Steps', empty: 'No steps yet.', delay: 'Delay before step (ms)', duration: 'Duration (ms)',
    text: 'Text', parameter: 'Parameter name', clear: 'Clear existing text first', replace: 'Replace selected steps with text parameter',
    replacementText: 'Recorded text or default value', replacementParameter: 'Parameter name, e.g. keyword',
    addWait: 'Add wait', addAssert: 'Add text assertion', addText: 'Add text input', save: 'Save workflow', name: 'Workflow name',
    library: 'My Workflows', run: 'Fill parameters and replay', delete: 'Delete workflow', wait: 'Wait', assert: 'Text assertion',
    selectHint: 'Select consecutive keyboard taps, then enter the text and a parameter name. You can change the keyword on each replay.',
    replay: 'Start replay', status: 'Run status', completed: 'Completed', failed: 'Failed', running: 'Running',
    saved: 'Workflow saved.', missingDevice: 'Choose a connected device first.', missingName: 'Enter a workflow name.',
    missingSteps: 'Add at least one step.', missingSelection: 'Select consecutive tap steps first.',
    invalidParameter: 'Parameter name must start with a letter and contain only letters, numbers and underscores.',
    deleteConfirm: 'Delete this workflow?', recordLost: 'Recording ended; you can still edit and save it.',
    remove: 'Remove', up: 'Move up', down: 'Move down', select: 'Select', physicalNote: 'Physical touches are recorded. Keyboard keys appear as taps; replace them with a text step before saving. Do not record passwords.',
    recorded: 'Recorded', tap: 'Tap', long_press: 'Long press', swipe: 'Swipe', back: 'Back', home: 'Home', textStep: 'Enter text',
    noTemplates: 'No saved workflows yet.', refreshTemplates: 'Refresh workflows', saveFirst: 'Save the workflow first.', discard: 'Discard recording', cancel: 'Stop replay', cancelled: 'Stopped'
  }
} as const;

type TextKey = keyof typeof TEXT.zh;

@Component({
  selector: 'app-workflows',
  standalone: true,
  imports: [FormsModule],
  templateUrl: './workflows.component.html',
  styleUrl: './workflows.component.scss',
  changeDetection: ChangeDetectionStrategy.Eager
})
export class WorkflowsComponent implements OnInit, OnDestroy {
  private readonly api = inject(ManualWorkflowService);
  readonly language = inject(UiLanguageService);
  readonly devices = signal<WorkflowDevice[]>([]);
  readonly templates = signal<WorkflowTemplate[]>([]);
  readonly recording = signal<PhysicalRecording | null>(null);
  readonly steps = signal<WorkflowStep[]>([]);
  readonly selected = signal<Set<number>>(new Set());
  readonly templateId = signal<string | null>(null);
  readonly run = signal<WorkflowRun | null>(null);
  readonly busy = signal(false);
  readonly message = signal('');
  readonly error = signal('');
  readonly parameters = computed(() => [...new Set(this.steps().map(step => step.parameter).filter((item): item is string => !!item))]);

  selectedDevice = '';
  name = '';
  replacementText = '';
  replacementParameter = 'keyword';
  parameterValues: Record<string, string> = {};
  private recordingTimer: ReturnType<typeof setInterval> | null = null;
  private runTimer: ReturnType<typeof setInterval> | null = null;

  /** 页面新建或刷新后继续显示同一浏览器会话里的录制。 */
  ngOnInit(): void {
    void this.refreshDevices();
    void this.refreshTemplates();
    const id = sessionStorage.getItem('artemis.physicalRecordingId');
    if (id) {
      void firstValueFrom(this.api.recording(id)).then(state => {
        if (!state) return;
        this.recording.set(state);
        this.selectedDevice = state.device_serial;
        this.steps.set([...state.steps]);
        if (state.active) this.startRecordingPoll();
      }).catch(() => sessionStorage.removeItem('artemis.physicalRecordingId'));
    }
  }

  ngOnDestroy(): void {
    this.clearRecordingPoll();
    this.clearRunPoll();
  }

  t(key: TextKey): string {
    return TEXT[this.language.language()][key];
  }

  /** 保留服务端错误详情，便于定位设备权限或占用问题。 */
  private showError(error: unknown): void {
    const value = error as { error?: { detail?: string }; message?: string };
    this.error.set(value?.error?.detail || value?.message || String(error));
    this.message.set('');
  }

  async refreshDevices(): Promise<void> {
    try {
      const response = await firstValueFrom(this.api.devices());
      this.devices.set(response.devices.filter(device => device.state === 'device'));
      if (!this.selectedDevice && this.devices().length === 1) this.selectedDevice = this.devices()[0].serial;
    } catch (error) { this.showError(error); }
  }

  async refreshTemplates(): Promise<void> {
    try { this.templates.set(await firstValueFrom(this.api.list())); }
    catch (error) { this.showError(error); }
  }

  async startRecording(): Promise<void> {
    if (!this.selectedDevice) { this.error.set(this.t('missingDevice')); return; }
    this.busy.set(true);
    this.error.set('');
    try {
      const state = await firstValueFrom(this.api.startRecording(this.selectedDevice));
      this.recording.set(state);
      this.steps.set([]);
      this.selected.set(new Set());
      this.templateId.set(null);
      this.name = '';
      this.run.set(null);
      sessionStorage.setItem('artemis.physicalRecordingId', state.id);
      this.startRecordingPoll();
    } catch (error) { this.showError(error); }
    finally { this.busy.set(false); }
  }

  private startRecordingPoll(): void {
    this.clearRecordingPoll();
    this.recordingTimer = setInterval(() => void this.pollRecording(), 800);
  }

  private async pollRecording(): Promise<void> {
    const current = this.recording();
    if (!current?.active) return;
    try {
      const state = await firstValueFrom(this.api.recording(current.id));
      this.recording.set(state);
      this.steps.set([...state.steps]);
      if (!state.active) {
        this.clearRecordingPoll();
        this.message.set(this.t('recordLost'));
      }
    } catch (error) { this.showError(error); this.clearRecordingPoll(); }
  }

  async stopRecording(): Promise<void> {
    const current = this.recording();
    if (!current) return;
    this.busy.set(true);
    try {
      const state = await firstValueFrom(this.api.stopRecording(current.id));
      this.recording.set(state);
      this.steps.set([...state.steps]);
      this.clearRecordingPoll();
    } catch (error) { this.showError(error); }
    finally { this.busy.set(false); }
  }

  /** 放弃草稿时明确释放录制持有的设备锁。 */
  async discardRecording(): Promise<void> {
    const current = this.recording();
    if (!current) return;
    try {
      await firstValueFrom(this.api.discardRecording(current.id));
      this.recording.set(null);
      this.steps.set([]);
      this.selected.set(new Set());
      sessionStorage.removeItem('artemis.physicalRecordingId');
      this.clearRecordingPoll();
    } catch (error) { this.showError(error); }
  }

  private clearRecordingPoll(): void {
    if (this.recordingTimer) clearInterval(this.recordingTimer);
    this.recordingTimer = null;
  }

  /** 让用户把一段软键盘点击替换为可编辑的输入动作。 */
  replaceSelectedWithText(): void {
    const indexes = [...this.selected()].sort((a, b) => a - b);
    if (!indexes.length || indexes.some((item, index) => index > 0 && item !== indexes[index - 1] + 1)
      || indexes.some(index => this.steps()[index]?.type !== 'tap')) {
      this.error.set(this.t('missingSelection'));
      return;
    }
    const parameter = this.replacementParameter.trim();
    if (parameter && !/^[A-Za-z][A-Za-z0-9_]*$/.test(parameter)) {
      this.error.set(this.t('invalidParameter'));
      return;
    }
    const first = indexes[0];
    const replacement: WorkflowStep = {
      type: 'text', delay_ms: this.steps()[first].delay_ms, duration_ms: 100,
      text: this.replacementText, parameter: parameter || null, clear_existing: true
    };
    const next = [...this.steps()];
    next.splice(first, indexes.length, replacement);
    this.steps.set(next);
    this.selected.set(new Set());
    if (parameter) this.parameterValues[parameter] = this.replacementText;
    this.error.set('');
  }

  toggleSelected(index: number): void {
    const selected = new Set(this.selected());
    if (selected.has(index)) selected.delete(index); else selected.add(index);
    this.selected.set(selected);
  }

  stepName(type: WorkflowStepType): string {
    const mapping: Record<WorkflowStepType, TextKey> = {
      tap: 'tap', long_press: 'long_press', swipe: 'swipe', text: 'textStep',
      back: 'back', home: 'home', assert_text: 'assert', wait: 'wait'
    };
    return this.t(mapping[type]);
  }

  addStep(type: 'wait' | 'assert_text' | 'text' | 'back' | 'home'): void {
    this.steps.update(steps => [...steps, {
      type, delay_ms: 0, duration_ms: type === 'assert_text' ? 5000 : 1000,
      text: type === 'assert_text' ? '预期文字' : '', clear_existing: true
    }]);
  }

  removeStep(index: number): void {
    this.steps.update(steps => steps.filter((_, i) => i !== index));
    this.selected.set(new Set());
  }

  moveStep(index: number, direction: -1 | 1): void {
    const next = [...this.steps()];
    const other = index + direction;
    if (other < 0 || other >= next.length) return;
    [next[index], next[other]] = [next[other], next[index]];
    this.steps.set(next);
    this.selected.set(new Set());
  }

  loadTemplate(template: WorkflowTemplate): void {
    if (this.recording()?.active) return;
    this.templateId.set(template.id);
    this.recording.set(null);
    sessionStorage.removeItem('artemis.physicalRecordingId');
    this.name = template.name;
    this.selectedDevice = template.device_serial;
    this.steps.set(template.steps.map(step => ({ ...step })));
    this.parameterValues = {};
    for (const step of template.steps) if (step.parameter) this.parameterValues[step.parameter] = step.text;
    this.selected.set(new Set());
    this.error.set('');
    this.message.set('');
  }

  /** 保存的是用户审阅后的顺序，原始键盘坐标不会自动替换。 */
  async save(): Promise<WorkflowTemplate | null> {
    if (!this.name.trim()) { this.error.set(this.t('missingName')); return null; }
    if (!this.steps().length) { this.error.set(this.t('missingSteps')); return null; }
    if (this.recording()?.active) { this.error.set(this.t('instructions')); return null; }
    this.busy.set(true);
    this.error.set('');
    try {
      const template = this.recording()
        ? await firstValueFrom(this.api.saveRecording(this.recording()!.id, this.name, this.steps()))
        : this.templateId()
          ? await firstValueFrom(this.api.update(this.templateId()!, this.name, this.steps()))
          : null;
      if (!template) { this.error.set(this.t('saveFirst')); return null; }
      this.templateId.set(template.id);
      this.recording.set(null);
      sessionStorage.removeItem('artemis.physicalRecordingId');
      this.message.set(this.t('saved'));
      await this.refreshTemplates();
      return template;
    } catch (error) { this.showError(error); return null; }
    finally { this.busy.set(false); }
  }

  async replay(): Promise<void> {
    if (!this.selectedDevice) { this.error.set(this.t('missingDevice')); return; }
    const template = await this.save();
    if (!template) return;
    this.busy.set(true);
    try {
      const state = await firstValueFrom(this.api.run(template.id, this.selectedDevice, { ...this.parameterValues }));
      this.run.set(state);
      this.startRunPoll();
    } catch (error) { this.showError(error); }
    finally { this.busy.set(false); }
  }

  private startRunPoll(): void {
    this.clearRunPoll();
    this.runTimer = setInterval(() => void this.pollRun(), 800);
  }

  private async pollRun(): Promise<void> {
    const current = this.run();
    if (!current || current.status !== 'running') return;
    try {
      const state = await firstValueFrom(this.api.runStatus(current.id));
      this.run.set(state);
      if (state.status !== 'running') this.clearRunPoll();
    } catch (error) { this.showError(error); this.clearRunPoll(); }
  }

  private clearRunPoll(): void {
    if (this.runTimer) clearInterval(this.runTimer);
    this.runTimer = null;
  }

  async cancelReplay(): Promise<void> {
    const current = this.run();
    if (!current || current.status !== 'running') return;
    try { await firstValueFrom(this.api.cancelRun(current.id)); }
    catch (error) { this.showError(error); }
  }

  async deleteTemplate(): Promise<void> {
    const id = this.templateId();
    if (!id || !confirm(this.t('deleteConfirm'))) return;
    try {
      await firstValueFrom(this.api.delete(id));
      this.templateId.set(null);
      this.name = '';
      this.steps.set([]);
      await this.refreshTemplates();
    } catch (error) { this.showError(error); }
  }
}
