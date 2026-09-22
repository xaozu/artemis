import { ChangeDetectionStrategy, Component, inject, OnDestroy, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { firstValueFrom } from 'rxjs';
import { AppExplorationService, ExplorationEdge, ExplorationTask } from '../../core/services/app-exploration.service';
import { ManualWorkflowService, WorkflowDevice } from '../../core/services/manual-workflow.service';
import { UiLanguageService } from '../../core/services/ui-language.service';

const TEXT = {
  zh: {
    title: 'App 自动探索', intro: '从启动页逐个探索可点击入口，保存页面分叉、受阻原因和可回放路径。',
    create: '创建探索任务', taskName: '任务名称', package: 'Android 包名', device: '目标设备', depth: '最大分叉深度',
    add: '创建', tasks: '探索任务', empty: '还没有探索任务。', roles: '账号角色', role: '角色名称',
    username: '登录账号', password: '登录密码', saveRole: '保存角色凭据', credentialNote: '凭据在本机加密保存。切换角色若遇到旧账号登录态，请先在手机退出旧账号。',
    explore: '继续探索', stop: '停止', all: '整包回放', module: '模块', runModule: '运行模块',
    tree: '页面分叉树', blocked: '受阻', retry: '重新尝试', saveModule: '保存模块', document: '下载探索文档',
    inputValue: '输入参数', saveInput: '保存输入值', inputNote: '普通输入参数会保存在任务中，请勿在此填写密码。',
    pending: '待探索', explored: '已探索', unchanged: '页面未变化', running: '运行中', completed: '已完成',
    failed: '失败', stopped: '已停止', partial: '有待处理分支', noDevice: '请先选择设备。', noTask: '请先创建或选择任务。',
    modelNote: '模块由模型建议，可手动调整。页面入口文字会发送给当前配置的模型；账号密码不会发送。',
    status: '当前状态', branchCount: '分支', pageCount: '页面', root: '打开 App', select: '选择任务'
  },
  en: {
    title: 'Explore an App', intro: 'Explore clickable entries from launch, saving a page tree, blockers and replayable paths.',
    create: 'Create exploration', taskName: 'Task name', package: 'Android package', device: 'Target device', depth: 'Maximum depth',
    add: 'Create', tasks: 'Exploration tasks', empty: 'No exploration tasks yet.', roles: 'Account roles', role: 'Role name',
    username: 'Login account', password: 'Login password', saveRole: 'Save role credentials', credentialNote: 'Credentials stay encrypted locally. If the app retains another account, sign out on the phone before switching roles.',
    explore: 'Continue exploring', stop: 'Stop', all: 'Run entire suite', module: 'Module', runModule: 'Run module',
    tree: 'Page branches', blocked: 'Blocked', retry: 'Retry', saveModule: 'Save module', document: 'Download report',
    inputValue: 'Input parameter', saveInput: 'Save input', inputNote: 'Ordinary input values are stored in the task. Do not enter passwords here.',
    pending: 'Pending', explored: 'Explored', unchanged: 'No page change', running: 'Running', completed: 'Completed',
    failed: 'Failed', stopped: 'Stopped', partial: 'Branches need attention', noDevice: 'Select a device.', noTask: 'Create or select a task first.',
    modelNote: 'The model suggests modules; you can edit them. Entry labels go to the configured model; credentials do not.',
    status: 'Status', branchCount: 'branches', pageCount: 'pages', root: 'Open App', select: 'Select task'
  }
} as const;
type TextKey = keyof typeof TEXT.zh;

@Component({
  selector: 'app-explorations', standalone: true, imports: [FormsModule],
  templateUrl: './explorations.component.html', styleUrl: './explorations.component.scss',
  changeDetection: ChangeDetectionStrategy.Eager
})
export class ExplorationsComponent implements OnInit, OnDestroy {
  private readonly api = inject(AppExplorationService);
  private readonly devicesApi = inject(ManualWorkflowService);
  readonly language = inject(UiLanguageService);
  readonly devices = signal<WorkflowDevice[]>([]);
  readonly tasks = signal<ExplorationTask[]>([]);
  readonly task = signal<ExplorationTask | null>(null);
  readonly error = signal('');
  readonly busy = signal(false);
  readonly moduleNames = signal<string[]>([]);
  readonly moduleEdits: Record<string, string> = {};
  readonly inputEdits: Record<string, string> = {};
  name = '';
  packageName = '';
  deviceSerial = '';
  maxDepth = 4;
  role = '默认';
  roleName = '';
  username = '';
  password = '';
  selectedModule = '';
  private timer: ReturnType<typeof setInterval> | null = null;

  /** 当前语言的页面文字。 */
  t(key: TextKey): string { return TEXT[this.language.language()][key]; }

  /** 将任务和分支状态换成页面文字。 */
  statusLabel(status: string): string {
    return status in TEXT.zh ? this.t(status as TextKey) : status;
  }

  /** 读取设备和已有任务，保留刷新前选中的探索树。 */
  ngOnInit(): void {
    void this.refresh();
    void firstValueFrom(this.devicesApi.devices()).then(value => {
      this.devices.set(value.devices.filter(device => device.state === 'device'));
      if (this.devices().length === 1) this.deviceSerial = this.devices()[0].serial;
    }).catch(error => this.showError(error));
    this.timer = setInterval(() => void this.poll(), 1500);
  }

  /** 离开页面时停止状态轮询。 */
  ngOnDestroy(): void { if (this.timer) clearInterval(this.timer); }

  private showError(error: unknown): void {
    const value = error as { error?: { detail?: string }; message?: string };
    this.error.set(value?.error?.detail || value?.message || String(error));
  }

  /** 保持选中任务与页面分叉树同步，刷新浏览器后仍可继续探索。 */
  async refresh(): Promise<void> {
    try {
      const tasks = await firstValueFrom(this.api.list());
      this.tasks.set(tasks);
      const id = this.task()?.id || sessionStorage.getItem('artemis.explorationTaskId');
      if (id && tasks.some(item => item.id === id)) await this.select(id);
    } catch (error) { this.showError(error); }
  }

  /** 读取任务并同步角色、模块和可修改的输入参数。 */
  async select(id: string): Promise<void> {
    try {
      const task = await firstValueFrom(this.api.get(id));
      this.task.set(task);
      this.deviceSerial = task.device_serial;
      sessionStorage.setItem('artemis.explorationTaskId', id);
      if (!task.roles.some(item => item.name === this.role)) this.role = task.roles[0]?.name || '默认';
      const modules = [...new Set(task.edges.filter(edge => edge.role === this.role).map(edge => edge.module))];
      this.moduleNames.set(modules);
      if (!modules.includes(this.selectedModule)) this.selectedModule = modules[0] || '';
      for (const edge of task.edges) this.moduleEdits[edge.id] = edge.module;
      for (const edge of task.edges) if (edge.action.parameter) this.inputEdits[edge.id] = task.parameters?.[edge.action.parameter] || '';
    } catch (error) { this.showError(error); }
  }

  /** 创建任务时显式绑定设备与 App 包名。 */
  async create(): Promise<void> {
    if (!this.deviceSerial) { this.error.set(this.t('noDevice')); return; }
    this.busy.set(true); this.error.set('');
    try {
      const task = await firstValueFrom(this.api.create(this.name, this.packageName, this.deviceSerial, this.maxDepth));
      await this.refresh();
      await this.select(task.id);
    } catch (error) { this.showError(error); }
    finally { this.busy.set(false); }
  }

  /** 提交后立即清空输入框，页面状态只保留角色名称。 */
  async saveRole(): Promise<void> {
    const task = this.task();
    if (!task) { this.error.set(this.t('noTask')); return; }
    this.busy.set(true); this.error.set('');
    try {
      await firstValueFrom(this.api.saveRole(task.id, this.roleName, this.username, this.password));
      this.role = this.roleName;
      this.username = ''; this.password = '';
      await this.select(task.id);
    } catch (error) { this.showError(error); }
    finally { this.busy.set(false); }
  }

  /** 探索继续未完成分支，回放可选择单模块或全部路径。 */
  async run(mode: 'explore' | 'replay', module: string | null = null): Promise<void> {
    const task = this.task();
    if (!task) { this.error.set(this.t('noTask')); return; }
    this.busy.set(true); this.error.set('');
    try { this.task.set(await firstValueFrom(this.api.run(task.id, this.role, mode, module))); }
    catch (error) { this.showError(error); }
    finally { this.busy.set(false); }
  }

  /** 请求后台在下一个动作前停止。 */
  async stop(): Promise<void> {
    const task = this.task(); if (!task) return;
    try { this.task.set(await firstValueFrom(this.api.stop(task.id))); }
    catch (error) { this.showError(error); }
  }

  /** 人工调整某个分支及其子分支的模块。 */
  async saveModule(edge: ExplorationEdge): Promise<void> {
    const task = this.task(); if (!task) return;
    try { await firstValueFrom(this.api.updateModule(task.id, edge.id, this.moduleEdits[edge.id])); await this.select(task.id); }
    catch (error) { this.showError(error); }
  }

  /** 前提条件已补齐后重新尝试受阻分支。 */
  async retry(edge: ExplorationEdge): Promise<void> {
    const task = this.task(); if (!task) return;
    try { await firstValueFrom(this.api.retry(task.id, edge.id)); await this.select(task.id); }
    catch (error) { this.showError(error); }
  }

  /** 普通输入步骤可保存后继续探索，密码只允许放在角色凭据中。 */
  async saveInput(edge: ExplorationEdge): Promise<void> {
    const task = this.task(); if (!task) return;
    try { await firstValueFrom(this.api.setInput(task.id, edge.id, this.inputEdits[edge.id])); await this.select(task.id); }
    catch (error) { this.showError(error); }
  }

  /** 下载服务端生成的 Markdown；不通过前端拼接敏感任务数据。 */
  downloadDocument(): void {
    const task = this.task(); if (!task) return;
    const anchor = document.createElement('a');
    anchor.href = this.api.documentUrl(task.id);
    anchor.download = `${task.name}.md`;
    anchor.click();
  }

  /** 页面卡片只显示直接从该页伸出的分支。 */
  edgesFor(nodeId: string): ExplorationEdge[] {
    return (this.task()?.edges || []).filter(edge => edge.from_node === nodeId);
  }

  private async poll(): Promise<void> {
    const task = this.task();
    if (task?.run?.status === 'running') await this.select(task.id);
  }
}
