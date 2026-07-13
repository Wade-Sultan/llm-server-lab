"use client"
import {
  createUserWithEmailAndPassword,
  signOut as firebaseSignOut,
  updatePassword as firebaseUpdatePassword,
  GoogleAuthProvider,
  onAuthStateChanged,
  sendPasswordResetEmail,
  signInWithEmailAndPassword,
  signInWithPopup,
  type User,
  updateProfile,
} from "firebase/auth"
import { useRouter } from "next/navigation"
import { useCallback, useEffect, useState } from "react"
import { syncAccount } from "@/lib/commerce"
import { auth } from "@/lib/firebase"
import { wakeUpBuilder } from "@/lib/wake-up-builder"

/**
 * Fire-and-forget sync of the Postgres users row (via the commerce service)
 * after any successful authentication. Failures are ignored — chat
 * auto-provisions the row as a fallback.
 */
function syncBackendAccount(user: User) {
  user
    .getIdToken()
    .then(syncAccount)
    .catch(() => undefined)
}

interface UseAuthReturn {
  user: User | null
  loading: boolean
  signIn: (email: string, password: string) => Promise<{ error: Error | null }>
  signInWithGoogle: () => Promise<{ error: Error | null }>
  signUp: (
    email: string,
    password: string,
    fullName?: string,
  ) => Promise<{ error: Error | null }>
  signOut: () => Promise<void>
  resetPassword: (email: string) => Promise<{ error: Error | null }>
  updatePassword: (newPassword: string) => Promise<{ error: Error | null }>
}

/**
 * Check if there's an active session without subscribing to changes.
 * Useful for route guards (beforeLoad) where hooks can't be used.
 */
export async function isLoggedIn(): Promise<boolean> {
  return new Promise((resolve) => {
    const unsubscribe = onAuthStateChanged(auth, (user) => {
      unsubscribe()
      resolve(user !== null)
    })
  })
}

/**
 * Get the current ID token for passing to your FastAPI backend.
 * Returns null if no active session.
 */
export async function getAccessToken(): Promise<string | null> {
  const user = auth.currentUser
  if (!user) return null
  return user.getIdToken()
}

export default function useAuth(): UseAuthReturn {
  const router = useRouter()
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const unsubscribe = onAuthStateChanged(auth, (user) => {
      setUser(user)
      setLoading(false)
    })
    return () => unsubscribe()
  }, [])

  const signIn = useCallback(
    async (email: string, password: string) => {
      try {
        const { user } = await signInWithEmailAndPassword(auth, email, password)
        syncBackendAccount(user)
        wakeUpBuilder()
        router.push("/build/new")
        return { error: null }
      } catch (err) {
        return { error: err as Error }
      }
    },
    [router],
  )

  const signInWithGoogle = useCallback(async () => {
    try {
      const { user } = await signInWithPopup(auth, new GoogleAuthProvider())
      syncBackendAccount(user)
      wakeUpBuilder()
      router.push("/build/new")
      return { error: null }
    } catch (err) {
      return { error: err as Error }
    }
  }, [router])

  const signUp = useCallback(
    async (email: string, password: string, fullName?: string) => {
      try {
        const { user } = await createUserWithEmailAndPassword(
          auth,
          email,
          password,
        )
        if (fullName) {
          await updateProfile(user, { displayName: fullName })
        }
        syncBackendAccount(user)
        wakeUpBuilder()
        router.push("/login")
        return { error: null }
      } catch (err) {
        return { error: err as Error }
      }
    },
    [router],
  )

  const signOut = useCallback(async () => {
    await firebaseSignOut(auth)
    router.push("/login")
  }, [router])

  const resetPassword = useCallback(async (email: string) => {
    try {
      await sendPasswordResetEmail(auth, email, {
        url: `${window.location.origin}/reset-password`,
      })
      return { error: null }
    } catch (err) {
      return { error: err as Error }
    }
  }, [])

  const updatePassword = useCallback(
    async (newPassword: string) => {
      try {
        if (!auth.currentUser) throw new Error("No authenticated user")
        await firebaseUpdatePassword(auth.currentUser, newPassword)
        router.push("/build/new")
        return { error: null }
      } catch (err) {
        return { error: err as Error }
      }
    },
    [router],
  )

  return {
    user,
    loading,
    signIn,
    signInWithGoogle,
    signUp,
    signOut,
    resetPassword,
    updatePassword,
  }
}
