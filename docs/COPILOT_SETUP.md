# Microsoft 365 Copilot setup

[← Back to the project](../README.md) · [User guide](USER_GUIDE.md)

This guide is for the optional direct API integration. Basic caption capture and
the manual copy-to-Copilot/ChatGPT workflow do not need this setup.

Copilot summarization is optional and disabled by default. The transcript stays local unless you manually request a summary or set both `enabled` and `auto_summarize` to `true` in `copilot-config.json`.

## Tenant prerequisites

- Each user needs a Microsoft 365 Copilot add-on license.
- A basic Microsoft 365 Copilot Chat entitlement can use the tray app's manual **Copy latest for basic Copilot Chat** handoff, but Microsoft currently does not allow it to call the Graph Chat API.
- An Entra administrator must permit a public-client app registration and may need to grant tenant-wide admin consent.
- The Microsoft 365 Copilot Chat API is a preview API under Microsoft Graph `/beta`; Microsoft does not support beta APIs for production use and can change the contract.

## Entra application registration

1. In the Microsoft Entra admin center, create a single-tenant app registration.
2. Under **Authentication**, add the **Mobile and desktop applications** platform with redirect URI `http://localhost`.
3. Enable **Allow public client flows**.
4. Under **API permissions > Microsoft Graph > Delegated permissions**, add all of the following:

   - `Sites.Read.All`
   - `Mail.Read`
   - `People.Read.All`
   - `OnlineMeetingTranscript.Read.All`
   - `Chat.Read`
   - `ChannelMessage.Read.All`
   - `ExternalItem.Read.All`

5. Grant admin consent if required by organizational policy.
6. Record the **Application (client) ID** and **Directory (tenant) ID**. Do not create or distribute a client secret; this is a delegated desktop/public-client flow.

## Configure the tray app

1. Right-click the tray icon and select **Configure Copilot…**. The app creates and opens `copilot-config.json` beside the executable.
2. Replace `YOUR_TENANT_ID` and `YOUR_CLIENT_ID` with the recorded IDs.
3. Set both `enabled` and `auto_summarize` to `true` for automatic summaries after meetings. Keep `enabled` false to allow only the manual **Summarize latest transcript** command. The [example configuration](../examples/copilot-config.example.json) uses placeholders only.
4. Save the JSON file.
5. Right-click the tray icon and choose **Sign in / test Copilot**. A Microsoft sign-in browser opens. Consent may require an administrator.

Successful summaries are written beside the transcript using `-summary.md`. Authentication tokens are cached under `%LOCALAPPDATA%\Teams Caption Notes` and encrypted using Windows DPAPI. Web-search grounding is disabled for summary requests.

If authentication, licensing, consent, networking, or the preview API fails, the original local transcript remains intact and the error is written to `teams-caption-notes.log`.
