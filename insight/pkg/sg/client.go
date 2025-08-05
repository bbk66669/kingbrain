package sg

import (
    "bytes"
    "encoding/json"
    "errors"
    "net/http"
    "os"
    "time"
)

type Client struct {
    primary   string
    fallback  string
    token     string
    httpClient *http.Client
}

// New returns a Client that will first try SG_URL, then LOCAL_SG_ENDPOINT.
func New() *Client {
    return &Client{
        primary:  os.Getenv("SG_URL"),
        fallback: os.Getenv("LOCAL_SG_ENDPOINT"),
        token:    os.Getenv("SG_TOKEN"),
        httpClient: &http.Client{ Timeout: 5 * time.Second },
    }
}

// GraphQL runs the given query+variables, trying primary then fallback.
func (c *Client) GraphQL(q string, v map[string]any, out any) error {
    payload := map[string]any{
        "query":     q,
        "variables": v,
    }
    body, err := json.Marshal(payload)
    if err != nil {
        return err
    }

    // helper to do one request
    doReq := func(url string) (*http.Response, error) {
        req, err := http.NewRequest("POST", url+"/.api/graphql", bytes.NewReader(body))
        if err != nil {
            return nil, err
        }
        req.Header.Set("Authorization", "token "+c.token)
        req.Header.Set("Content-Type", "application/json")
        return c.httpClient.Do(req)
    }

    // try primary
    if c.primary != "" {
        resp, err := doReq(c.primary)
        if err == nil && resp.StatusCode < 300 {
            defer resp.Body.Close()
            return json.NewDecoder(resp.Body).Decode(out)
        }
        if resp != nil {
            resp.Body.Close()
        }
    }

    // fallback
    if c.fallback != "" {
        resp, err := doReq(c.fallback)
        if err == nil && resp.StatusCode < 300 {
            defer resp.Body.Close()
            return json.NewDecoder(resp.Body).Decode(out)
        }
        if resp != nil {
            resp.Body.Close()
        }
    }

    return errors.New("GraphQL request failed on both primary and fallback endpoints")
}
