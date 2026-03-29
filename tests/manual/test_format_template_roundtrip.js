/**
 * Regression test: format template roundtrip (save → reload → still selected)
 *
 * Bug: prebid/salesagent#1165
 * When saving a product with Video/Display template selected, the checkbox
 * appeared unchecked on reload because _parseInitialFormats didn't map expanded
 * format IDs (e.g. "video_standard") back to their parent template ("video").
 *
 * This test verifies the full roundtrip:
 * 1. Create a product with Video template + 15s duration selected
 * 2. Navigate to the edit page
 * 3. Verify the Video template card has class "selected"
 * 4. Verify the 15s duration button has class "selected"
 *
 * Prerequisites:
 *   - Docker stack running: docker compose up -d
 *   - Playwright installed: npx playwright install chromium
 *
 * Run:
 *   node tests/manual/test_format_template_roundtrip.js
 */

const { chromium } = require('playwright');

const TARGET_URL = 'http://localhost:8000';

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  let passed = true;

  function assert(condition, message) {
    if (!condition) {
      console.error(`  FAIL: ${message}`);
      passed = false;
    } else {
      console.log(`  PASS: ${message}`);
    }
  }

  try {
    // 1. Login
    console.log('1. Logging in...');
    await page.goto(TARGET_URL + '/test/login');
    await page.waitForLoadState('networkidle');
    const loginButton = page.locator('button:has-text("Log in as Admin"), a:has-text("Log in as Admin")');
    if (await loginButton.isVisible({ timeout: 5000 })) {
      await loginButton.click();
      await page.waitForLoadState('networkidle');
    }

    // 2. Navigate to add product
    console.log('2. Creating product with Video template...');
    await page.goto(TARGET_URL + '/tenant/default/products/add');
    await page.waitForLoadState('networkidle');

    // 3. Select Video template
    const videoTemplate = page.locator('.template-card:has-text("Video")');
    assert(await videoTemplate.isVisible({ timeout: 5000 }), 'Video template card visible');
    await videoTemplate.click();
    await page.waitForTimeout(300);

    // 4. Select 15s duration
    const duration15s = page.locator('.duration-btn:has-text("15s")');
    if (await duration15s.isVisible({ timeout: 2000 })) {
      await duration15s.click();
      console.log('  Selected 15s duration');
    }
    await page.waitForTimeout(300);

    // 5. Verify hidden input has expanded format IDs
    const hiddenInput = page.locator('#formats-data');
    const formatsJson = await hiddenInput.inputValue();
    const formats = JSON.parse(formatsJson);
    console.log(`  Hidden input has ${formats.length} format entries`);
    const hasVideoStandard = formats.some(f => f.id === 'video_standard');
    const hasVideoVast = formats.some(f => f.id === 'video_vast');
    assert(hasVideoStandard, 'Expanded formats include video_standard');
    assert(hasVideoVast, 'Expanded formats include video_vast');

    // 6. Fill required fields and submit
    await page.fill('input[name="name"]', 'Roundtrip Test Product - Video');
    await page.fill('textarea[name="description"]', 'Regression test for #1165');

    const pricingModelSelect = page.locator('select[name="pricing_model_0"]');
    if (await pricingModelSelect.isVisible()) {
      await pricingModelSelect.selectOption('cpm_fixed');
      await page.waitForTimeout(300);
    }
    const rateInput = page.locator('input[name="rate_0"]');
    if (await rateInput.isVisible()) await rateInput.fill('10.00');

    const currencySelect = page.locator('select[name="currency_0"]');
    if (await currencySelect.isVisible()) await currencySelect.selectOption('USD');

    // Select property tag
    const propertyTagCheckbox = page.locator('input[name="selected_property_tags"]').first();
    if (await propertyTagCheckbox.isVisible({ timeout: 2000 }).catch(() => false)) {
      await propertyTagCheckbox.check();
    }

    // Submit
    await page.evaluate(() => document.querySelector('form')?.submit());
    await page.waitForTimeout(3000);

    // 7. Find the created product and navigate to edit
    console.log('3. Navigating to edit page...');
    const currentUrl = page.url();
    if (currentUrl.includes('/products') && !currentUrl.includes('/add')) {
      console.log('  Product created, redirected to:', currentUrl);
    }

    // Find the edit link for our product
    const editLink = page.locator('a:has-text("Roundtrip Test Product - Video")').first();
    if (await editLink.isVisible({ timeout: 5000 })) {
      await editLink.click();
      await page.waitForLoadState('networkidle');
      await page.waitForTimeout(1000);
    } else {
      // Try finding any edit link on the products list
      console.log('  Product link not visible, checking page...');
      await page.screenshot({ path: '/tmp/roundtrip-debug.png', fullPage: true });
    }

    // 8. THE KEY ASSERTION: Video template should still be selected on reload
    console.log('4. Verifying Video template is selected after reload...');
    const videoCard = page.locator('.template-card:has-text("Video")');
    assert(await videoCard.isVisible({ timeout: 5000 }), 'Video template card visible on edit page');

    const videoCardSelected = await videoCard.evaluate(
      el => el.classList.contains('selected')
    );
    assert(videoCardSelected, 'Video template card has "selected" class after reload (bug #1165)');

    // 9. Verify duration is still selected
    const duration15sOnReload = page.locator('.duration-btn:has-text("15s")');
    if (await duration15sOnReload.isVisible({ timeout: 2000 })) {
      const durationSelected = await duration15sOnReload.evaluate(
        el => el.classList.contains('selected')
      );
      assert(durationSelected, '15s duration button still selected after reload');
    }

    // 10. Verify hidden input still has the expanded format IDs
    const reloadedFormatsJson = await page.locator('#formats-data').inputValue();
    const reloadedFormats = JSON.parse(reloadedFormatsJson);
    const stillHasVideoStandard = reloadedFormats.some(f => f.id === 'video_standard');
    const stillHasVideoVast = reloadedFormats.some(f => f.id === 'video_vast');
    assert(stillHasVideoStandard, 'Reloaded formats still include video_standard');
    assert(stillHasVideoVast, 'Reloaded formats still include video_vast');

    await page.screenshot({ path: '/tmp/roundtrip-result.png', fullPage: true });

  } catch (error) {
    console.error('ERROR:', error.message);
    await page.screenshot({ path: '/tmp/roundtrip-error.png', fullPage: true });
    passed = false;
  } finally {
    await browser.close();
  }

  console.log(passed ? '\n✅ All assertions passed' : '\n❌ Some assertions failed');
  process.exit(passed ? 0 : 1);
})();
