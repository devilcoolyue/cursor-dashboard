<script setup lang="ts">
import { computed } from 'vue'
import {
  ExternalLink, KeyRound, Trash2, ListFilter, ArrowRightLeft, LayoutGrid, Search, RefreshCw, Clock3, SlidersHorizontal, UserRound, Building2,
  Sun, Moon, Monitor, ChevronRight, ChevronDown, ChevronsUpDown, Check, ArrowDownUp,
  Mail, Copy, ShieldCheck, Plus, Menu, X, UsersRound, Tags, Ellipsis, PanelLeftClose, Palette, LogOut, Server, PanelsTopLeft,
} from '@lucide/vue'
const props = withDefaults(defineProps<{ name: string; size?: number }>(), { size: 16 })
const icons = {
  mail: Mail, copy: Copy, shield: ShieldCheck, plus: Plus, external: ExternalLink, key: KeyRound, trash: Trash2, detail: ListFilter, switch: ArrowRightLeft, grid: LayoutGrid, search: Search, refresh: RefreshCw, clock: Clock3, sliders: SlidersHorizontal,
  user: UserRound, building: Building2, server: Server, sun: Sun, moon: Moon, monitor: Monitor,
  chevron: ChevronRight, chevronDown: ChevronDown, chevrons: ChevronsUpDown, check: Check,
  sort: ArrowDownUp, menu: Menu, close: X, users: UsersRound, tags: Tags, more: Ellipsis,
  panel: PanelLeftClose, palette: Palette, logout: LogOut, panels: PanelsTopLeft,
}
const icon = computed(() => icons[props.name as keyof typeof icons] || LayoutGrid)
</script>
<template><component :is="icon" :size="size" :stroke-width="1.65" class="ui-icon" aria-hidden="true" focusable="false" /></template>
<style>
.ui-icon { display: block; flex-shrink: 0; vertical-align: middle; }
</style>
