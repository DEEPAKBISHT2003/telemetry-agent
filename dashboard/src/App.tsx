import { useState } from "react";
import type { DateRangePreset, NavigationTab } from "./types/analytics";
import { getStoredAuth } from "./api/client";
import { Sidebar } from "./components/Sidebar";
import { Header } from "./components/Header";
import { AuthModal } from "./components/AuthModal";
import { OverviewPage } from "./pages/OverviewPage";
import { DevelopersPage } from "./pages/DevelopersPage";
import { ModelsPage } from "./pages/ModelsPage";
import { SessionsPage } from "./pages/SessionsPage";
import { DevicesPage } from "./pages/DevicesPage";

export const App: React.FC = () => {
  const [activeTab, setActiveTab] = useState<NavigationTab>("overview");
  const [dateRange, setDateRange] = useState<DateRangePreset>("30d");
  const [refreshTrigger, setRefreshTrigger] = useState<number>(0);
  const [isAuthModalOpen, setIsAuthModalOpen] = useState<boolean>(false);
  const [authKey, setAuthKey] = useState<string>(getStoredAuth());

  const handleRefresh = () => {
    setRefreshTrigger((prev) => prev + 1);
  };

  const handleAuthSaved = () => {
    setAuthKey(getStoredAuth());
    handleRefresh();
  };

  const authPreview = authKey
    ? authKey.startsWith("dvt_")
      ? `${authKey.slice(0, 8)}...`
      : "Admin Key Active"
    : "No Auth";

  return (
    <div className="app-container">
      <div className="atmospheric-bg" />

      {/* Navigation Sidebar */}
      <Sidebar activeTab={activeTab} onSelectTab={setActiveTab} />

      {/* Main Content Area */}
      <div className="main-content">
        <Header
          activeTab={activeTab}
          dateRange={dateRange}
          onSelectDateRange={setDateRange}
          onRefresh={handleRefresh}
          onOpenAuth={() => setIsAuthModalOpen(true)}
          hasAuth={Boolean(authKey)}
          authPreview={authPreview}
        />

        <main style={{ flex: 1 }}>
          {activeTab === "overview" && (
            <OverviewPage
              dateRange={dateRange}
              refreshTrigger={refreshTrigger}
              onNavigateToDevelopers={() => setActiveTab("developers")}
              onNavigateToModels={() => setActiveTab("models")}
            />
          )}

          {activeTab === "developers" && (
            <DevelopersPage
              dateRange={dateRange}
              refreshTrigger={refreshTrigger}
            />
          )}

          {activeTab === "models" && (
            <ModelsPage
              dateRange={dateRange}
              refreshTrigger={refreshTrigger}
            />
          )}

          {activeTab === "sessions" && (
            <SessionsPage
              dateRange={dateRange}
              refreshTrigger={refreshTrigger}
            />
          )}

          {activeTab === "devices" && (
            <DevicesPage refreshTrigger={refreshTrigger} />
          )}
        </main>
      </div>

      {/* Auth Configuration Modal */}
      <AuthModal
        isOpen={isAuthModalOpen}
        onClose={() => setIsAuthModalOpen(false)}
        onSave={handleAuthSaved}
      />
    </div>
  );
};

export default App;
