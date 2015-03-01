// func to check user input percentages dont add up to more than 100 and to work out other percentage

function userInputCheck(inputform, country){
  var $form = $(inputform)
  $sumpercentages = $form.find(".inputnumbers");

  var sum = 0
  $sumpercentages.each(function() {
    var value = Number($(this).val());
    if (!isNaN(value)) sum += value;
  });

  if (sum >= 100){
    otherPercentage = "< 0"
    alert("Percentages add up to more than 100!");
  }



  else
    otherPercentage = 100 - sum

  spanId = "#other" + country

  $(spanId).html(otherPercentage);

}


//for the map, change values in seatData object

// the vote totals, page state changes what the vote totals show
