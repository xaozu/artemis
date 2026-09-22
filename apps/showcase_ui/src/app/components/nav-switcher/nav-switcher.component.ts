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

import { Component, ChangeDetectionStrategy, inject } from '@angular/core';

import { RouterLink, RouterLinkActive } from '@angular/router';
import { UiLanguageService } from '../../core/services/ui-language.service';

@Component({
  selector: 'app-nav-switcher',
  standalone: true,
  imports: [RouterLink, RouterLinkActive],
  template: `
    <nav class="floating-nav-switcher" [attr.aria-label]="language.text('nav.main')">
      <a 
        routerLink="/" 
        routerLinkActive="active" 
        [routerLinkActiveOptions]="{exact: true}"
        class="nav-tab-btn"
        [title]="language.text('nav.homeTitle')"
      >
        <span class="material-symbols-outlined tab-icon">add_task</span>
        <span class="tab-label">{{ language.text('nav.home') }}</span>
      </a>
      <a 
        routerLink="/workspace" 
        routerLinkActive="active" 
        class="nav-tab-btn"
        [title]="language.text('nav.workspaceTitle')"
      >
        <span class="material-symbols-outlined tab-icon">space_dashboard</span>
        <span class="tab-label">{{ language.text('nav.workspace') }}</span>
      </a>
      <a routerLink="/workflows" routerLinkActive="active" class="nav-tab-btn"
        [title]="language.text('nav.workflowsTitle')">
        <span class="material-symbols-outlined tab-icon">gesture</span>
        <span class="tab-label">{{ language.text('nav.workflows') }}</span>
      </a>
      <button type="button" class="nav-tab-btn language-switch"
        (click)="toggleLanguage()"
        [title]="language.language() === 'en' ? language.text('nav.switchToChinese') : language.text('nav.switchToEnglish')"
        [attr.aria-label]="language.language() === 'en' ? language.text('nav.switchToChinese') : language.text('nav.switchToEnglish')">
        {{ language.language() === 'en' ? '中文' : 'EN' }}
      </button>
    </nav>
  `,
  changeDetection: ChangeDetectionStrategy.Eager,
  styleUrls: ['./nav-switcher.component.scss']
})
export class NavSwitcherComponent {
  readonly language = inject(UiLanguageService);

  /** 顶部入口负责切换整个网页的关键操作文案。 */
  toggleLanguage(): void {
    this.language.setLanguage(this.language.language() === 'en' ? 'zh' : 'en');
  }
}
