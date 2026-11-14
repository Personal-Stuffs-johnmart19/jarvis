/** All chat surfaces resolve the same live approval; stale and repeated decisions are ignored. */
export function createApprovalResponder(
  isPending: (id: string) => boolean,
  respond: (id: string, approved: boolean) => void | PromiseLike<void>,
) {
  const consumed = new Set<string>();
  return async (id: string, approved: boolean): Promise<boolean> => {
    if (consumed.has(id) || !isPending(id)) return false;
    consumed.add(id);
    try {
      await respond(id, approved);
      return true;
    } catch (error) {
      consumed.delete(id);
      throw error;
    }
  };
}
