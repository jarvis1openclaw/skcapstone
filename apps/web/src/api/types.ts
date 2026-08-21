/**
 * Typed shapes returned by the SKLegal API for the application shell.
 * All records are tenant-scoped; matter records are matter-scoped.
 */

export interface ClientSummary {
  id: string;
  tenantId: string;
  displayName: string;
  matterCount: number;
}

export interface MatterSummary {
  id: string;
  tenantId: string;
  clientId: string;
  clientDisplayName: string;
  title: string;
  status: "active" | "on_hold" | "closed";
}

export interface ClientDetail extends ClientSummary {
  matters: readonly MatterSummary[];
}

export interface MatterDetail extends MatterSummary {
  openedOn: string;
  description: string;
}
