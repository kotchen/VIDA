import { mockDashboard } from "./mock"
import type { DashboardData } from "./types"

export interface DataProvider {
  getDashboard(): Promise<DashboardData>
}

export class MockProvider implements DataProvider {
  async getDashboard(): Promise<DashboardData> {
    return mockDashboard
  }
}

export const dataProvider: DataProvider = new MockProvider()
