import { redirect } from "next/navigation";

// The dashboard no longer needs a sign-in.
export default function LoginPage() {
  redirect("/app");
}
