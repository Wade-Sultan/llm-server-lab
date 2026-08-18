"use client"

import ChangePassword from "@/components/UserSettings/ChangePassword"
import DeleteAccount from "@/components/UserSettings/DeleteAccount"
import PriceAlerts from "@/components/UserSettings/PriceAlerts"
import UserInformation from "@/components/UserSettings/UserInformation"
import useAuth from "@/hooks/useAuth"

export default function SettingsPage() {
  const { user } = useAuth()

  if (!user) return null

  return (
    <div className="flex flex-col gap-6 px-8 py-6">
      <h1 className="text-2xl font-bold tracking-tight">Settings</h1>
      <UserInformation />
      <PriceAlerts />
      <ChangePassword />
      <DeleteAccount />
    </div>
  )
}
