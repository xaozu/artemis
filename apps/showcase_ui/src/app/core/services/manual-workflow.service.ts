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

import { inject, Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

export type WorkflowStepType = 'tap' | 'long_press' | 'swipe' | 'text' | 'back' | 'home' | 'assert_text' | 'wait';

/** 固化步骤只保存可回放的动作和可修改的文本参数。 */
export interface WorkflowStep {
  type: WorkflowStepType;
  delay_ms: number;
  x?: number | null;
  y?: number | null;
  x2?: number | null;
  y2?: number | null;
  duration_ms: number;
  text: string;
  parameter?: string | null;
  clear_existing: boolean;
}

export interface WorkflowTemplate {
  id: string;
  name: string;
  device_serial: string;
  screen_width: number;
  screen_height: number;
  created_at: number;
  steps: WorkflowStep[];
}

export interface PhysicalRecording {
  id: string;
  device_serial: string;
  screen_width: number;
  screen_height: number;
  active: boolean;
  steps: WorkflowStep[];
}

export interface WorkflowRun {
  id: string;
  status: 'running' | 'completed' | 'failed' | 'cancelled';
  step_index: number;
  step_count: number;
  error: string | null;
}

export interface WorkflowDevice {
  serial: string;
  model?: string | null;
  state: string;
  is_busy: boolean;
}

@Injectable({ providedIn: 'root' })
export class ManualWorkflowService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/manual-workflows';

  /** 设备必须由用户明确选择，录制和回放不自动切换到另一台手机。 */
  devices(): Observable<{ devices: WorkflowDevice[] }> {
    return this.http.get<{ devices: WorkflowDevice[] }>('/api/devices');
  }

  list(): Observable<WorkflowTemplate[]> {
    return this.http.get<WorkflowTemplate[]>(this.base);
  }

  startRecording(deviceSerial: string): Observable<PhysicalRecording> {
    return this.http.post<PhysicalRecording>(`${this.base}/recordings`, { device_serial: deviceSerial });
  }

  recording(id: string): Observable<PhysicalRecording> {
    return this.http.get<PhysicalRecording>(`${this.base}/recordings/${id}`);
  }

  stopRecording(id: string): Observable<PhysicalRecording> {
    return this.http.post<PhysicalRecording>(`${this.base}/recordings/${id}/stop`, {});
  }

  discardRecording(id: string): Observable<{ deleted: boolean }> {
    return this.http.delete<{ deleted: boolean }>(`${this.base}/recordings/${id}`);
  }

  saveRecording(id: string, name: string, steps: WorkflowStep[]): Observable<WorkflowTemplate> {
    return this.http.post<WorkflowTemplate>(`${this.base}/recordings/${id}/save`, { name, steps });
  }

  update(id: string, name: string, steps: WorkflowStep[]): Observable<WorkflowTemplate> {
    return this.http.put<WorkflowTemplate>(`${this.base}/${id}`, { name, steps });
  }

  delete(id: string): Observable<{ deleted: boolean }> {
    return this.http.delete<{ deleted: boolean }>(`${this.base}/${id}`);
  }

  run(id: string, deviceSerial: string, parameters: Record<string, string>): Observable<WorkflowRun> {
    return this.http.post<WorkflowRun>(`${this.base}/${id}/runs`, { device_serial: deviceSerial, parameters });
  }

  runStatus(id: string): Observable<WorkflowRun> {
    return this.http.get<WorkflowRun>(`${this.base}/runs/${id}/status`);
  }

  cancelRun(id: string): Observable<WorkflowRun> {
    return this.http.post<WorkflowRun>(`${this.base}/runs/${id}/cancel`, {});
  }
}
