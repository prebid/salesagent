const { chromium } = require('playwright');

const TARGET_URL = 'http://localhost:8000';

(async () => {
  const browser = await chromium.launch({ headless: false, slowMo: 200 });
  const page = await browser.newPage();

  try {
    console.log('1. Navigating to test login page...');
    await page.goto(TARGET_URL + '/test/login');
    await page.waitForLoadState('networkidle');
    await page.screenshot({ path: '/tmp/e2e-01-login.png', fullPage: true });

    // Login using test mode - click "Log in as Admin" button
    console.log('2. Logging in as Admin...');
    const loginButton = page.locator('button:has-text("Log in as Admin"), a:has-text("Log in as Admin")');
    if (await loginButton.isVisible({ timeout: 5000 })) {
      await loginButton.click();
      await page.waitForLoadState('networkidle');
      await page.waitForTimeout(1000);
    } else {
      console.log('   Login button not found, trying form submit...');
      const form = page.locator('form');
      await form.first().evaluate(f => f.submit());
      await page.waitForLoadState('networkidle');
    }
    await page.screenshot({ path: '/tmp/e2e-02-after-login.png', fullPage: true });

    // Navigate to products list directly (correct URL pattern: /tenant/<tenant_id>/products)
    console.log('3. Going to Products page directly...');
    await page.goto(TARGET_URL + '/tenant/default/products');
    await page.waitForLoadState('networkidle');
    await page.screenshot({ path: '/tmp/e2e-03-products-list.png', fullPage: true });

    // Click Add Product
    console.log('4. Clicking Add Product...');
    const addProductLink = page.locator('a:has-text("Add Product")');
    if (await addProductLink.isVisible({ timeout: 5000 })) {
      await addProductLink.click();
      await page.waitForLoadState('networkidle');
      await page.waitForTimeout(1000);
    } else {
      // Go directly to add product page
      await page.goto(TARGET_URL + '/tenant/default/products/add');
      await page.waitForLoadState('networkidle');
      await page.waitForTimeout(1000);
    }
    await page.screenshot({ path: '/tmp/e2e-04-add-product.png', fullPage: true });

    // Check for format template picker
    console.log('5. Looking for Format Template Picker...');
    const picker = page.locator('#format-template-picker-container');
    const pickerVisible = await picker.isVisible({ timeout: 5000 }).catch(() => false);
    console.log('   Format picker container visible:', pickerVisible);

    if (pickerVisible) {
      // Scroll to format picker
      await picker.scrollIntoViewIfNeeded();
      await page.waitForTimeout(500);
      await page.screenshot({ path: '/tmp/e2e-05-format-picker.png', fullPage: true });

      // Look for format templates (class is .template-card)
      const templates = await page.locator('.template-card').all();
      console.log('   Found', templates.length, 'template cards');

      // Click on Display template (unified display covering image, HTML5, JS)
      console.log('6. Selecting Display template...');
      const displayTemplate = page.locator('.template-card:has-text("Display")');
      if (await displayTemplate.isVisible()) {
        await displayTemplate.click();
        await page.waitForTimeout(500);
        await page.screenshot({ path: '/tmp/e2e-06-display-selected.png', fullPage: true });

        // Select some sizes (class is .size-btn, not .size-button)
        console.log('7. Selecting sizes...');
        const sizeButtons = await page.locator('.size-btn').all();
        console.log('   Found', sizeButtons.length, 'size buttons');

        // Click on 300x250 (uses 'x' not '×')
        const size300x250 = page.locator('.size-btn:has-text("300x250")');
        if (await size300x250.isVisible()) {
          await size300x250.click();
          console.log('   Selected 300x250');
        }

        // Click on 728x90
        const size728x90 = page.locator('.size-btn:has-text("728x90")');
        if (await size728x90.isVisible()) {
          await size728x90.click();
          console.log('   Selected 728x90');
        }

        await page.waitForTimeout(500);
        await page.screenshot({ path: '/tmp/e2e-07-sizes-selected.png', fullPage: true });

        // Check hidden input value
        const hiddenInput = page.locator('#formats-data');
        const formatsValue = await hiddenInput.inputValue();
        console.log('8. Hidden input value:');
        console.log(formatsValue);

        // Fill in other required fields
        console.log('9. Filling in product details...');
        await page.fill('input[name="name"]', 'E2E Test Product');
        await page.fill('textarea[name="description"]', 'Testing format template picker');

        // Fill pricing - need to add a pricing option
        console.log('   Adding pricing option...');
        const addPricingBtn = page.locator('button:has-text("Add Pricing Option")');
        if (await addPricingBtn.isVisible()) {
          await addPricingBtn.click();
          await page.waitForTimeout(300);
        }

        // Fill the first pricing option
        // Select pricing model (cpm_fixed)
        const pricingModelSelect = page.locator('select[name="pricing_model_0"]');
        if (await pricingModelSelect.isVisible()) {
          await pricingModelSelect.selectOption('cpm_fixed');
          console.log('   Selected cpm_fixed pricing model');
          await page.waitForTimeout(300); // Wait for onchange handler
        }

        // Fill rate (input name depends on pricing model)
        const rateInput = page.locator('input[name="rate_0"]');
        if (await rateInput.isVisible()) {
          await rateInput.fill('10.00');
          console.log('   Set rate to 10.00');
        }

        // Currency should already be USD by default, but let's make sure
        const currencySelect = page.locator('select[name="currency_0"]');
        if (await currencySelect.isVisible()) {
          await currencySelect.selectOption('USD');
          console.log('   Selected USD currency');
        }

        // Select property tag (required field) - input name is "selected_property_tags"
        console.log('   Selecting property tag...');
        // First scroll the property tags section into view
        const propertyTagsSection = page.locator('#property-tags-section');
        if (await propertyTagsSection.isVisible({ timeout: 2000 }).catch(() => false)) {
          await propertyTagsSection.scrollIntoViewIfNeeded();
        }

        // Try to find any selected_property_tags checkbox
        const anyPropertyTagCheckbox = page.locator('input[name="selected_property_tags"]').first();
        if (await anyPropertyTagCheckbox.isVisible({ timeout: 2000 }).catch(() => false)) {
          await anyPropertyTagCheckbox.check();
          console.log('   Selected property tag checkbox');
        } else {
          console.log('   No selected_property_tags checkboxes found');
          // Log available checkboxes for debugging
          const allCheckboxes = await page.locator('input[type="checkbox"]').all();
          console.log('   Available checkboxes:', allCheckboxes.length);
        }

        await page.screenshot({ path: '/tmp/e2e-08-form-filled.png', fullPage: true });

        // Submit the form
        console.log('10. Submitting form...');

        // Listen for console errors
        page.on('console', msg => {
          if (msg.type() === 'error') {
            console.log('   [BROWSER ERROR]', msg.text());
          }
        });

        // Scroll to bottom to make button visible
        await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
        await page.waitForTimeout(500);

        const submitButton = page.locator('button[type="submit"]:has-text("Create Product")');
        const buttonVisible = await submitButton.isVisible({ timeout: 5000 }).catch(() => false);
        console.log('   Submit button visible:', buttonVisible);

        if (buttonVisible) {
          await submitButton.scrollIntoViewIfNeeded();

          // Try submitting the form directly instead of clicking the button
          console.log('   Submitting form via JavaScript...');
          await page.evaluate(() => {
            const form = document.querySelector('form');
            if (form) {
              console.log('Found form, submitting...');
              form.submit();
            } else {
              console.log('No form found!');
            }
          });

          await page.waitForTimeout(3000);
          await page.screenshot({ path: '/tmp/e2e-09-after-submit.png', fullPage: true });

          // Check for success or error message
          const errorMessage = page.locator('.alert-error, .flash-error, .error');
          const successMessage = page.locator('.alert-success, .flash-success, .success');

          if (await errorMessage.isVisible({ timeout: 2000 }).catch(() => false)) {
            const errorText = await errorMessage.textContent();
            console.log('❌ Error:', errorText);
          } else if (await successMessage.isVisible({ timeout: 2000 }).catch(() => false)) {
            const successText = await successMessage.textContent();
            console.log('✅ Success:', successText);
          } else {
            // Check URL for indication of success
            const currentUrl = page.url();
            console.log('   Current URL after submit:', currentUrl);
            if (currentUrl.includes('/products') && !currentUrl.includes('/add')) {
              console.log('✅ Product created successfully (redirected to products list)');
            }
          }
        }
      } else {
        console.log('   Display template not found!');
      }
    } else {
      console.log('   Format picker container NOT visible');
      // Check page content
      const pageContent = await page.content();
      console.log('   Page title:', await page.title());
    }

    console.log('\n✅ Test completed! Check screenshots in /tmp/');

  } catch (error) {
    console.error('❌ Error:', error.message);
    await page.screenshot({ path: '/tmp/e2e-error.png', fullPage: true });
  } finally {
    await browser.close();
  }
})();
