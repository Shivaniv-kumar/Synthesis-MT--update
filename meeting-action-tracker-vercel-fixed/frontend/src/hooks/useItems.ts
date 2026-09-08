import {
  useQuery,
  useMutation,
  useQueryClient,
  type UseQueryResult,
  type UseMutationResult,
} from '@tanstack/react-query'
import { getItems, updateItem, deleteItem, bulkSaveItems } from '@/api/items'
import type { ActionItem, ItemFilters, UpdateItemRequest, PaginatedResponse } from '@/types'

// ── Query key factory ─────────────────────────────────────────────────────────

const itemKeys = {
  all: ['items'] as const,
  list: (filters: ItemFilters) => ['items', filters] as const,
}

// ── useItems ──────────────────────────────────────────────────────────────────

/**
 * Fetches a paginated, filtered list of action items.
 */
export function useItems(
  filters: ItemFilters = {},
): UseQueryResult<PaginatedResponse<ActionItem>> {
  return useQuery({
    queryKey: itemKeys.list(filters),
    queryFn: () => getItems(filters),
    staleTime: 30_000,
  })
}

// ── useUpdateItem ─────────────────────────────────────────────────────────────

interface UpdateItemVariables {
  id: string
  data: UpdateItemRequest
}

/**
 * Mutation to partially update an action item, with optimistic cache update.
 */
export function useUpdateItem(): UseMutationResult<
  ActionItem,
  Error,
  UpdateItemVariables,
  { previousCaches: Array<[ItemFilters, PaginatedResponse<ActionItem> | undefined]> }
> {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ id, data }: UpdateItemVariables) => updateItem(id, data),

    onMutate: async ({ id, data }) => {
      // Cancel any in-flight refetches for all item queries
      await queryClient.cancelQueries({ queryKey: itemKeys.all })

      // Snapshot all matching caches for potential rollback
      const allCaches = queryClient.getQueriesData<PaginatedResponse<ActionItem>>({
        queryKey: itemKeys.all,
      })

      // Collect previous values for rollback
      const previousCaches: Array<[ItemFilters, PaginatedResponse<ActionItem> | undefined]> = []

      for (const [queryKey, previousData] of allCaches) {
        // queryKey shape: ['items', filters]
        const filters = (queryKey[1] ?? {}) as ItemFilters
        previousCaches.push([filters, previousData])

        if (previousData) {
          queryClient.setQueryData<PaginatedResponse<ActionItem>>(queryKey, {
            ...previousData,
            items: previousData.items.map((item) =>
              item.id === id ? { ...item, ...data } : item,
            ),
          })
        }
      }

      return { previousCaches }
    },

    onError: (_err, _vars, context) => {
      // Roll back all optimistic updates
      if (context) {
        for (const [filters, previousData] of context.previousCaches) {
          queryClient.setQueryData(itemKeys.list(filters), previousData)
        }
      }
    },

    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: itemKeys.all })
    },
  })
}

// ── useDeleteItem ─────────────────────────────────────────────────────────────

/**
 * Mutation to delete an action item by ID.
 * Invalidates all item queries on success.
 */
export function useDeleteItem(): UseMutationResult<void, Error, string> {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (id: string) => deleteItem(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: itemKeys.all })
    },
  })
}

// ── useBulkSave ───────────────────────────────────────────────────────────────

/**
 * Mutation to bulk-upsert action items.
 * Invalidates all item queries on success.
 */
export function useBulkSave(): UseMutationResult<ActionItem[], Error, ActionItem[]> {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (items: ActionItem[]) => bulkSaveItems(items),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: itemKeys.all })
    },
  })
}
