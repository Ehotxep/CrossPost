namespace CrossPost.Core;

public sealed class Workflow(Store store)
{
    public async Task<Publication> SendAsync(ITelegram telegram, string botId, AppOptions options, Draft draft, CancellationToken ct)
    {
        draft.Validate();
        var publication = new Publication(Guid.NewGuid(), DateTimeOffset.UtcNow, draft, options.Demo, botId,
            new(DeliveryState.Sending, options.Chat), draft.PlainText);
        await store.SavePublicationAsync(publication);
        Delivery result;
        try { result = await telegram.SendAsync(options.Chat, draft, ct); }
        catch (OperationCanceledException)
        { result = new(DeliveryState.Unknown, options.Chat, Error: "Отправка прервана. Проверьте канал перед повтором."); }
        catch (Exception)
        { result = new(DeliveryState.Unknown, options.Chat, Error: "Результат отправки неизвестен. Проверьте канал перед повтором."); }
        publication = publication with { Delivery = result };
        try { await store.SavePublicationAsync(publication); }
        catch (Exception error)
        { throw new InvalidOperationException("Результат не сохранён в истории. Не отправляйте повторно до проверки канала.", error); }
        return publication;
    }
    public async Task<(Publication Publication, EditResult Result)> EditAsync(ITelegram telegram, Publication publication, string text, CancellationToken ct)
    {
        // Persist attempted edit before network I/O so a restart cannot conceal uncertainty.
        var pending = publication with { Revision = publication.Revision + 1, EditState = "Результат изменения не подтверждён" };
        await store.SavePublicationAsync(pending);
        EditResult result;
        try { result = await telegram.EditAsync(publication, text, ct); }
        catch (OperationCanceledException) { result = new(false, true, "Редактирование прервано. Проверьте сообщение."); }
        catch (Exception error) { result = new(false, true, error.Message); }
        var updated = pending with { EditText = result.Confirmed ? text : publication.EditText,
            EditState = result.Confirmed ? "Изменено" : result.Unknown ? "Результат изменения неизвестен" : "Изменение отклонено" };
        await store.SavePublicationAsync(updated);
        return (updated, result);
    }
}
