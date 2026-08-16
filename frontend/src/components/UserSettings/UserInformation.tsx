"use client"
import { zodResolver } from "@hookform/resolvers/zod"
import { updateEmail, updateProfile } from "firebase/auth"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import { Button } from "@/components/ui/button"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { LoadingButton } from "@/components/ui/loading-button"
import useAuth from "@/hooks/useAuth"
import { auth } from "@/lib/firebase"
import { cn } from "@/lib/utils"

const formSchema = z.object({
  full_name: z.string().max(30).optional(),
  email: z.email({ message: "Invalid email address" }),
})

type FormData = z.infer<typeof formSchema>

const UserInformation = () => {
  const { user } = useAuth()
  const [editMode, setEditMode] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Firebase stores the display name directly on the User object
  const currentFullName = user?.displayName ?? ""
  const currentEmail = user?.email ?? ""

  const form = useForm<FormData>({
    resolver: zodResolver(formSchema),
    mode: "onBlur",
    criteriaMode: "all",
    defaultValues: {
      full_name: currentFullName,
      email: currentEmail,
    },
  })

  const toggleEditMode = () => {
    setEditMode(!editMode)
  }

  const onSubmit = async (data: FormData) => {
    if (submitting || !auth.currentUser) return
    setSubmitting(true)
    setError(null)

    try {
      if (data.full_name !== currentFullName) {
        await updateProfile(auth.currentUser, {
          displayName: data.full_name || null,
        })
      }

      // Update email if changed
      // Note: Firebase may require the user to re-authenticate before
      // changing their email. If this throws, you may need to prompt
      // the user for their password and call reauthenticateWithCredential first.
      if (data.email !== currentEmail) {
        await updateEmail(auth.currentUser, data.email)
      }

      setEditMode(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update profile")
    } finally {
      setSubmitting(false)
    }
  }

  const onCancel = () => {
    form.reset()
    setError(null)
    toggleEditMode()
  }

  return (
    <div className="max-w-md">
      <h3 className="text-lg font-semibold py-4">User Information</h3>
      <Form {...form}>
        <form
          onSubmit={form.handleSubmit(onSubmit)}
          className="flex flex-col gap-4"
        >
          {error && <p className="text-sm text-destructive">{error}</p>}

          <FormField
            control={form.control}
            name="full_name"
            render={({ field }) =>
              editMode ? (
                <FormItem>
                  <FormLabel>Full Name</FormLabel>
                  <FormControl>
                    <Input {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              ) : (
                <FormItem>
                  <FormLabel>Full Name</FormLabel>
                  <p
                    className={cn(
                      "text-sm",
                      !field.value && "text-muted-foreground",
                    )}
                  >
                    {field.value || "Not set"}
                  </p>
                </FormItem>
              )
            }
          />

          <FormField
            control={form.control}
            name="email"
            render={({ field }) =>
              editMode ? (
                <FormItem>
                  <FormLabel>Email</FormLabel>
                  <FormControl>
                    <Input type="email" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              ) : (
                <FormItem>
                  <FormLabel>Email</FormLabel>
                  <p className="text-sm">{field.value}</p>
                </FormItem>
              )
            }
          />

          <div className="flex gap-2">
            {editMode ? (
              <>
                <LoadingButton type="submit" loading={submitting}>
                  Save
                </LoadingButton>
                <Button type="button" variant="outline" onClick={onCancel}>
                  Cancel
                </Button>
              </>
            ) : (
              <Button type="button" onClick={toggleEditMode}>
                Edit
              </Button>
            )}
          </div>
        </form>
      </Form>
    </div>
  )
}

export default UserInformation
