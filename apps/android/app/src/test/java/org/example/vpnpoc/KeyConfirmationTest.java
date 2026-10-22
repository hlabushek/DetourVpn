package org.example.vpnpoc;

import org.amnezia.awg.crypto.KeyPair;
import org.junit.Test;
import static org.junit.Assert.*;

public final class KeyConfirmationTest {
    private static final String CONTEXT = "project-vpn:key-confirmation:v1:"
        + "01234567890123456789012345678901:" + "abcdef0123456789".repeat(4);
    @Test public void confirmationMatchesBothDirections() throws Exception {
        KeyPair client = new KeyPair(), server = new KeyPair();
        assertEquals(KeyConfirmation.answer(client, server.getPublicKey().toBase64(), CONTEXT),
                     KeyConfirmation.answer(server, client.getPublicKey().toBase64(), CONTEXT));
    }
    @Test public void lowOrderKeyFailsClosed() throws Exception {
        try {
            KeyConfirmation.answer(new KeyPair(), "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=", CONTEXT);
            fail();
        } catch (IllegalArgumentException expected) { }
    }
    @Test public void wrongContextFailsClosed() throws Exception {
        try {
            KeyConfirmation.answer(new KeyPair(), new KeyPair().getPublicKey().toBase64(), "arbitrary-message");
            fail();
        } catch (IllegalArgumentException expected) { }
    }
}
