export const PRODUCT_NAME = "SKLegal";

export function foundationLabel(environment: string): string {
  return `${PRODUCT_NAME} ${environment.trim()}`;
}
