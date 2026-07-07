import {
  Activity,
  Bookmark,
  Calendar,
  CreditCard,
  File,
  Image,
  Mail,
  MapPin,
  Megaphone,
  MessageSquare,
  StickyNote,
  User,
  type LucideIcon,
} from 'lucide-react'
import type { ItemKind } from '@/lib/api'

const KIND_ICONS: Record<ItemKind, LucideIcon> = {
  note: StickyNote,
  email: Mail,
  message: MessageSquare,
  photo: Image,
  file: File,
  event: Calendar,
  contact: User,
  location: MapPin,
  transaction: CreditCard,
  bookmark: Bookmark,
  post: Megaphone,
  activity: Activity,
}

export function KindIcon({ kind, className }: { kind: ItemKind; className?: string }) {
  const Icon = KIND_ICONS[kind]
  return <Icon aria-hidden className={className} />
}
