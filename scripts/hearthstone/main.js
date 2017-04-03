function getData(){

	$.when(
		$.getJSON("cards_simple.json", function(data){

			cardData = data;
		})
	).then(function(){
		pageLoad();
	})
};

function pageLoad(){
	$.each(cardData, function(card, data){
		data["filtered"] = true;

		if (dict.mode.standard.indexOf(data.set) != -1){
			data.mode = "standard";
		} else {
			data.mode = "wild"
		}
	})

	// run filter and write table
	filters.resetFilter();
	filters.filter();
	filters.displayCard("f5d6/rel/OG_311.png")

}

var cardData = [];

function initialization(){

	$(document).ready(function(){
		getData();
	});
}

initialization();
