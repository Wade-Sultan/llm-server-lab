"use client"
import { useState } from "react"
import { useForm } from "react-hook-form"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { LoadingButton } from "@/components/ui/loading-button"
import useAuth, { getAccessToken } from "@/hooks/useAuth"
import { deleteAccount } from "@/lib/commerce"

const DeleteConfirmation = () => {
  const { handleSubmit } = useForm()
  const { signOut } = useAuth()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const onSubmit = async () => {
    setLoading(true)
    setError(null)

    try {
      const token = await getAccessToken()
      if (!token) {
        await signOut()
        return
      }

      await deleteAccount(token)

      await signOut()
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong")
    } finally {
      setLoading(false)
    }
  }

  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button variant="destructive" className="mt-3">
          Delete Account
        </Button>
      </DialogTrigger>
      <DialogContent>
        <form onSubmit={handleSubmit(onSubmit)}>
          <DialogHeader>
            <DialogTitle>Confirmation Required</DialogTitle>
            <DialogDescription>
              All your account data will be{" "}
              <strong>permanently deleted.</strong> If you are sure, please
              click <strong>&quot;Confirm&quot;</strong> to proceed. This action
              cannot be undone.
            </DialogDescription>
          </DialogHeader>
          {error && <p className="text-sm text-destructive mt-2">{error}</p>}
          <DialogFooter className="mt-4 gap-2">
            <DialogClose asChild>
              <Button variant="outline" type="button">
                Cancel
              </Button>
            </DialogClose>
            <LoadingButton
              variant="destructive"
              type="submit"
              loading={loading}
            >
              Confirm
            </LoadingButton>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

export default DeleteConfirmation
