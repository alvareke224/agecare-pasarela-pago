// Tipos y llamadas del módulo de Facturación (espejo de app/billing/schemas.py)
import { ADMIN, BILLING_APP, api, qs, request } from "./client";

export type PlanCode = "free" | "gold" | "platinum" | "provider";
export type SubscriptionStatus = "incomplete" | "active" | "past_due" | "canceled" | "expired";
export type ChargeStatus = "pending" | "authorized" | "failed" | "unknown" | "refunded" | "partially_refunded";
export type ChargeKind = "initial" | "renewal" | "retry" | "manual";

export interface Page<T> { items: T[]; page: number; page_size: number; total: number }

export interface PaymentMethod {
  id: string;
  card_type: string | null;
  card_last4: string | null;
  status: "pending" | "active" | "failed" | "aborted" | "removed";
  activated_at: string | null;
  provider?: string;
  failure_code?: string | null;
  removed_at?: string | null;
}

export interface Subscription {
  id: string;
  plan_code: PlanCode;
  plan_name: string;
  status: SubscriptionStatus;
  status_name: string;
  beneficiary_type: string;
  beneficiary_id: string;
  price_amount: number;
  currency_code: string;
  current_period_start: string | null;
  current_period_end: string | null;
  next_charge_at: string | null;
  cancel_at_period_end: boolean;
  pending_plan_code: PlanCode | null;
  retry_count: number;
  started_at: string | null;
  canceled_at: string | null;
  cancel_reason: string | null;
  payment_method: PaymentMethod | null;
  created_at: string;
}

export interface AdminSubscription extends Subscription {
  user_id: string;
  user_email: string;
  user_name: string | null;
  cancel_note: string | null;
}

export interface Charge {
  id: string;
  subscription_id: string;
  plan_code: PlanCode;
  kind: ChargeKind;
  buy_order: string;
  amount: number;
  refunded_amount: number;
  currency_code: string;
  status: ChargeStatus;
  period_start: string;
  period_end: string;
  card_last4: string | null;
  authorization_code: string | null;
  transaction_date: string | null;
  failure_message: string | null;
  created_at: string;
}

export interface AdminCharge extends Charge {
  user_id: string;
  user_email: string;
  attempt: number;
  response_code: number | null;
  failure_code: string | null;
  commerce_code: string;
  detail_buy_order: string;
  resolved_at: string | null;
  requested_by: string | null;
}

export interface Refund {
  id: string;
  charge_id: string;
  amount: number;
  status: "succeeded" | "failed";
  refund_type: "REVERSED" | "NULLIFIED" | null;
  authorization_code: string | null;
  reason: string;
  failure_message: string | null;
  requested_by_name: string | null;
  created_at: string;
}

export interface BillingEvent {
  id: number;
  type: string;
  actor_type: "user" | "admin" | "system";
  actor_id: string | null;
  charge_id: string | null;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface Summary {
  currency_code: string;
  mrr: number;
  plans: { plan_code: PlanCode; plan_name: string; active: number; past_due: number; mrr: number }[];
  subscriptions: { active: number; past_due: number; incomplete: number; cancel_scheduled: number; canceled_30d: number; started_30d: number };
  charges_30d: { approved: number; failed: number; approval_rate: number | null; gross_revenue: number; refunded: number; net_revenue: number };
  upcoming_7d: { count: number; amount: number };
  unresolved_charges: number;
  gateway: string;
  computed_at: string;
}

export interface CycleStats {
  reconciled: number; renewed: number; recovered: number; failed: number; unknown: number;
  canceled: number; expired_subscriptions: number; expired_inscriptions: number; errors: string[];
}

export const billingAdmin = {
  summary: () => api.get<Summary>(`${ADMIN}/billing/summary`),
  subscriptions: (f: { status?: string; plan?: string; q?: string; cancel_scheduled?: boolean; page?: number }) =>
    api.get<Page<AdminSubscription>>(`${ADMIN}/billing/subscriptions${qs({ ...f, page_size: 20 })}`),
  subscription: (id: string) =>
    api.get<{ subscription: AdminSubscription; charges: AdminCharge[]; events: BillingEvent[] }>(`${ADMIN}/billing/subscriptions/${id}`),
  cancel: (id: string, body: { immediate: boolean; reason: string; note: string }) =>
    api.post<AdminSubscription>(`${ADMIN}/billing/subscriptions/${id}/cancel`, body),
  extend: (id: string, body: { days: number; reason: string }) =>
    api.post<AdminSubscription>(`${ADMIN}/billing/subscriptions/${id}/extend`, body),
  retry: (id: string) => api.post<AdminCharge>(`${ADMIN}/billing/subscriptions/${id}/retry-charge`),
  charges: (f: { status?: string; q?: string; subscription_id?: string; page?: number }) =>
    api.get<Page<AdminCharge>>(`${ADMIN}/billing/charges${qs({ ...f, page_size: 20 })}`),
  charge: (id: string) =>
    api.get<{ charge: AdminCharge; refunds: Refund[]; refundable_amount: number; gateway_response: unknown }>(`${ADMIN}/billing/charges/${id}`),
  refund: (id: string, body: { amount: number; reason: string }) =>
    api.post<Refund>(`${ADMIN}/billing/charges/${id}/refunds`, body),
  runCycle: () => api.post<CycleStats>(`${ADMIN}/billing/jobs/billing-cycle`),
  devAppToken: () =>
    api.post<{ token: string; email: string; family_id: string; provider_id: string }>(`${ADMIN}/billing/dev/app-token`),
};

// Endpoints de la app (usuario final). Se usan en la página de checkout de prueba.
export interface PlanOffer { plan_code: PlanCode; name: string; billing_unit: string; price_amount: number; currency_code: string }
export interface SubscribeOut {
  subscription: Subscription;
  next_action: "redirect_to_webpay" | "none";
  inscription: { url_webpay: string; token: string; method: "POST"; token_field: "TBK_TOKEN" } | null;
  charge: Charge | null;
}
export interface MeOut {
  entitlement: { plan_code: PlanCode; plan_name: string; subscription_id: string | null; valid_until: string | null };
  subscriptions: Subscription[];
  payment_method: PaymentMethod | null;
}

export const billingApp = {
  plans: () => request<{ plans: PlanOffer[] }>("GET", `${BILLING_APP}/plans`, undefined, ""),
  me: (token: string) => request<MeOut>("GET", `${BILLING_APP}/me`, undefined, token),
  subscribe: (token: string, plan_code: PlanCode, beneficiary_id?: string) =>
    request<SubscribeOut>("POST", `${BILLING_APP}/subscriptions`, { plan_code, beneficiary_id }, token),
  cancel: (token: string, id: string) => request<Subscription>("POST", `${BILLING_APP}/subscriptions/${id}/cancel`, {}, token),
  resume: (token: string, id: string) => request<Subscription>("POST", `${BILLING_APP}/subscriptions/${id}/resume`, {}, token),
  addCard: (token: string) =>
    request<{ url_webpay: string; token: string }>("POST", `${BILLING_APP}/payment-methods`, {}, token),
};

/** Webpay exige un formulario POST con TBK_TOKEN hacia url_webpay (no un GET). */
export function redirectToWebpay(url: string, token: string) {
  const form = document.createElement("form");
  form.method = "POST";
  form.action = url;
  const input = document.createElement("input");
  input.type = "hidden";
  input.name = "TBK_TOKEN";
  input.value = token;
  form.appendChild(input);
  document.body.appendChild(form);
  form.submit();
}
