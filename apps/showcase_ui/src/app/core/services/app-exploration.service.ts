import { inject, Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

/** 页面树中一条可点击分支，状态由服务端持久化。 */
export interface ExplorationEdge {
  id: string;
  role: string;
  from_node: string;
  to_node: string | null;
  action: { label: string; kind?: string; parameter?: string };
  module: string;
  status: 'pending' | 'explored' | 'blocked' | 'unchanged';
  reason: string | null;
}

export interface ExplorationNode {
  id: string;
  role: string;
  depth: number;
  title: string;
  path: { label: string }[];
}

export interface ExplorationTask {
  id: string;
  name: string;
  package: string;
  device_serial: string;
  max_depth: number;
  roles: { name: string; has_credentials: boolean }[];
  parameters: Record<string, string>;
  nodes: ExplorationNode[];
  edges: ExplorationEdge[];
  module_source: string;
  run: { id: string; role: string; mode: string; module: string | null; status: string; completed: number; error: string | null } | null;
}

@Injectable({ providedIn: 'root' })
export class AppExplorationService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/app-explorations';

  list(): Observable<ExplorationTask[]> { return this.http.get<ExplorationTask[]>(this.base); }
  get(id: string): Observable<ExplorationTask> { return this.http.get<ExplorationTask>(`${this.base}/${id}`); }
  create(name: string, packageName: string, deviceSerial: string, maxDepth: number): Observable<ExplorationTask> {
    return this.http.post<ExplorationTask>(this.base, { name, package: packageName, device_serial: deviceSerial, max_depth: maxDepth });
  }
  saveRole(id: string, role: string, username: string, password: string): Observable<ExplorationTask> {
    return this.http.put<ExplorationTask>(`${this.base}/${id}/roles`, { role, username, password });
  }
  updateModule(id: string, edgeId: string, module: string): Observable<ExplorationTask> {
    return this.http.put<ExplorationTask>(`${this.base}/${id}/edges/${edgeId}/module`, { module });
  }
  retry(id: string, edgeId: string): Observable<ExplorationTask> {
    return this.http.post<ExplorationTask>(`${this.base}/${id}/edges/${edgeId}/retry`, {});
  }
  setInput(id: string, edgeId: string, value: string): Observable<ExplorationTask> {
    return this.http.put<ExplorationTask>(`${this.base}/${id}/edges/${edgeId}/input`, { value });
  }
  run(id: string, role: string, mode: 'explore' | 'replay', module: string | null): Observable<ExplorationTask> {
    return this.http.post<ExplorationTask>(`${this.base}/${id}/runs`, { role, mode, module });
  }
  stop(id: string): Observable<ExplorationTask> { return this.http.post<ExplorationTask>(`${this.base}/${id}/runs/stop`, {}); }
  documentUrl(id: string): string { return `${this.base}/${id}/document`; }
}
